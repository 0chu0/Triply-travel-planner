"""
评测执行器：驱动 Agent 跑多轮对话，并把过程采集为「结构化 transcript」

设计要点
--------
1. **复用生产同一判据**：正文识别直接用 `app.api.v1.chat.is_answer_stream_event`，
   不复制一份判断逻辑 —— 否则评测采到的"正文"和线上用户看到的不是同一个东西，
   指标就失去意义（这是本方案最重要的一条约束）。
2. **零改动生产代码**：温度等评测参数通过「进程内替换 travel_agent.get_llm」实现，
   只在评测进程生效，不修改 app/ 下任何文件。
3. **隔离**：每个 case 用独立 thread_id（`eval-{run_id}-{case_id}`），
   并且统一使用专用评测用户 id，避免污染真实用户的长期记忆。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import HumanMessage

from app.api.v1.chat import is_answer_stream_event
from app.utils.logger import app_logger

from scripts.eval_lib.metrics import STEP_TRANSITIONS

# 诊断开关：EVAL_DEBUG_EVENTS=1 时打印每个流式事件的来源（默认关闭）
_DEBUG_STREAM_EVENTS = os.getenv("EVAL_DEBUG_EVENTS") == "1"

# 专用评测用户：与任何真实账号无关，长期记忆写入都落在它的命名空间下
DEFAULT_EVAL_USER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass
class CaseRun:
    """一条用例的执行结果（评测指标的输入）"""
    case_id: str
    title: str = ""
    status: str = "success"          # success | error | timeout
    error: Optional[str] = None
    turns: list[dict] = field(default_factory=list)
    steps_path: list[str] = field(default_factory=list)
    final_step: Optional[str] = None
    state: dict = field(default_factory=dict)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thread_id: str = ""
    started_at: str = ""

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "turns": self.turns,
            "steps_path": self.steps_path,
            "final_step": self.final_step,
            "state": self.state,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "thread_id": self.thread_id,
            "started_at": self.started_at,
        }


# ---------------------------------------------------------------- 环境准备

async def prepare_environment() -> None:
    """按 FastAPI lifespan 的顺序初始化 Checkpointer / Store / MCP（评测脚本自跑用）"""
    from app.core.checkpointer import CheckpointerManager
    from app.core.store import StoreManager
    from app.mcp_core.client import MCPClientManager

    await CheckpointerManager.get_instance()
    await StoreManager.get_instance()
    await MCPClientManager.get_instance()


async def shutdown_environment() -> None:
    """关闭 MCP 连接（评测进程退出前调用）"""
    try:
        from app.mcp_core.client import MCPClientManager

        if MCPClientManager._instance is not None:      # noqa: SLF001 - 脚本收尾
            await MCPClientManager._instance.close()    # noqa: SLF001
    except Exception as exc:  # pragma: no cover
        app_logger.warning(f"⚠️ 关闭 MCP 失败（已忽略）: {exc}")


def install_temperature_override(temperature: Optional[float]) -> None:
    """
    在评测进程内把全链路 LLM 温度固定到指定值（默认 0，保证可复现）。

    生产默认 temperature=0.7。本函数只设置 `app.core.llm` 的评测开关，
    **不改生产行为**（生产不调用本函数，开关恒为 None）。
    不传参（即 `temperature is None`，对应 `--temperature prod`）则完全不覆盖。

    ⚠️ 旧实现只替换 `travel_agent.get_llm`，会被 middleware 每轮的
    `request.override(model=get_main_llm())` 盖掉——报告写着 temperature=0、
    实际却跑在 0.7 上，"可复现"是假的（模型分档改造后尤其明显）。
    改为走工厂开关后，主对话 / 子 Agent / RAG 改写与重排全部生效。
    """
    if temperature is None:
        return

    import app.core.llm as llm_mod

    llm_mod.set_eval_temperature(temperature)
    app_logger.info(f"🔧 评测模式：全链路 LLM 温度固定为 {temperature}")


# ---------------------------------------------------------------- 单轮驱动

async def run_one_turn(
    agent,
    thread_id: str,
    user_message: str,
    user_id: str,
) -> dict:
    """驱动一轮对话，返回该轮的采集结果"""
    assistant = ""
    tools: list[str] = []
    tool_errors: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    started = time.perf_counter()
    # 诊断：被正文白名单放行、但其实来自子图（ns 含 "|"）的流式事件 —— 属于链路缺陷
    subgraph_events = 0
    subgraph_sample = ""

    run_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    # ⚠️ 不要往顶层 config 注入 metadata：实测它会干扰 LangGraph 往事件 metadata 里
    # 写入的 langgraph_node，使子图（目的地 Router 的 explore/weather 子 Agent，
    # 节点名同为 "model"）的流式输出穿过 chat.py 的正文白名单被采集进来。
    # 评测需要与生产逐字一致，因此这里只保留 configurable + callbacks。
    try:
        from app.core.tracing import get_langfuse_handler

        handler = get_langfuse_handler()
        if handler is not None:
            run_config["callbacks"] = [handler]
    except Exception:  # pragma: no cover - 追踪不可用不影响评测
        pass

    payload = {
        "messages": [HumanMessage(content=user_message)],
        "user_id": user_id,
    }

    async for event in agent.astream_events(payload, config=run_config, version="v2"):
        kind = event.get("event")

        if kind == "on_chat_model_stream":
            # 诊断开关（默认关）：EVAL_DEBUG_EVENTS=1 时打印每个流式事件来源，
            # 用于排查「正文白名单」是否把子图输出误判为正文。
            if _DEBUG_STREAM_EVENTS:
                _md = event.get("metadata") or {}
                _chunk = event.get("data", {}).get("chunk")
                if getattr(_chunk, "content", None):
                    app_logger.info(
                        f"[dbg] node={_md.get('langgraph_node')!r} "
                        f"ns={_md.get('langgraph_checkpoint_ns')!r} "
                        f"content={str(_chunk.content)[:30]!r}"
                    )
            # 与生产同一判据：只认主图 model 节点的流式输出
            if not is_answer_stream_event(event):
                continue
            chunk = event.get("data", {}).get("chunk")
            if chunk is not None and getattr(chunk, "content", None):
                token = chunk.content
                if not isinstance(token, str):
                    token = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in token
                    )
                # 诊断：这一条虽然通过了生产判据，但若 ns 含 "|" 说明它其实来自子图
                # （如目的地 Router 的 explore 子 Agent）——即「内部文本泄漏进正文」，
                # 属链路缺陷证据，记下来写进报告，便于一眼看出失败不是模型的问题。
                _ns = str((event.get("metadata") or {}).get("langgraph_checkpoint_ns") or "")
                if token and "|" in _ns:
                    subgraph_events += 1
                    if not subgraph_sample:
                        subgraph_sample = token[:40]
                assistant += token

        elif kind == "on_chat_model_end":
            output = event.get("data", {}).get("output")
            usage = (
                output.get("usage_metadata")
                if isinstance(output, dict)
                else getattr(output, "usage_metadata", None)
            )
            if usage:
                prompt_tokens += int(usage.get("input_tokens") or 0)
                completion_tokens += int(usage.get("output_tokens") or 0)

        elif kind == "on_tool_start":
            tools.append(event.get("name") or "")

        elif kind == "on_tool_error":
            tool_errors.append(event.get("name") or "")

        await asyncio.sleep(0)

    return {
        "user": user_message,
        "assistant": assistant,
        "tools": [t for t in tools if t],
        "tool_errors": [t for t in tool_errors if t],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "subgraph_events": subgraph_events,
        "subgraph_sample": subgraph_sample,
    }


def build_steps_path(tool_names: list[str]) -> list[str]:
    """从状态转移工具的调用顺序推断步骤路径（比只看终态更有信息量）"""
    path = ["requirement_collection"]
    for name in tool_names:
        target = STEP_TRANSITIONS.get(name)
        if target and path[-1] != target:
            path.append(target)
    return path


async def read_state_snapshot(agent, thread_id: str) -> dict:
    """读取图状态里的关键字段（current_step / user_requirement）"""
    try:
        state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        values = getattr(state, "values", None) or {}
    except Exception as exc:  # pragma: no cover
        app_logger.warning(f"⚠️ 读取图状态失败（已忽略）: {exc}")
        return {}

    req = values.get("user_requirement")
    if req is not None and not isinstance(req, dict):
        req = dict(req) if hasattr(req, "keys") else {}

    return {
        # current_step 只在状态转移工具被调用后才写进图状态；未推进时与
        # middleware 的默认值保持一致（middleware 用 state.get(..., "requirement_collection")）
        "current_step": values.get("current_step") or "requirement_collection",
        "user_requirement": req or {},
        "selected_destination": values.get("selected_destination"),
        "has_itinerary": bool(values.get("itinerary")),
        "has_budget": bool(values.get("budget")),
    }


# ---------------------------------------------------------------- 单用例执行

async def run_case(
    case,
    run_id: str,
    *,
    timeout_sec: int = 300,
    max_turns: Optional[int] = None,
    user_id: str = DEFAULT_EVAL_USER_ID,
) -> CaseRun:
    """执行一条用例：多轮驱动 + 状态采集 + 步骤路径推断"""
    from app.agents.handoffs.travel_agent import create_travel_agent

    thread_id = f"eval-{run_id}-{case.id}"
    result = CaseRun(
        case_id=case.id,
        title=case.title,
        thread_id=thread_id,
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )

    turns = list(case.turns)
    if max_turns and len(turns) > max_turns:
        turns = turns[:max_turns]

    started = time.perf_counter()
    try:
        agent = await create_travel_agent()

        for user_message in turns:
            turn = await asyncio.wait_for(
                run_one_turn(agent, thread_id, user_message, user_id),
                timeout=timeout_sec,
            )
            result.turns.append(turn)
            result.prompt_tokens += turn["prompt_tokens"]
            result.completion_tokens += turn["completion_tokens"]

        snapshot = await read_state_snapshot(agent, thread_id)
        result.state = snapshot
        result.final_step = snapshot.get("current_step")
        result.steps_path = build_steps_path(
            [t for turn in result.turns for t in turn["tools"]]
        )

    except asyncio.TimeoutError:
        result.status = "timeout"
        result.error = f"单轮执行超过 {timeout_sec}s"
        app_logger.warning(f"⏱️ 用例超时: {case.id}")
    except Exception as exc:
        result.status = "error"
        result.error = f"{type(exc).__name__}: {exc}"
        app_logger.exception(f"❌ 用例执行失败: {case.id}")

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


async def run_cases(
    cases: list,
    run_id: str,
    *,
    concurrency: int = 2,
    timeout_sec: int = 300,
    max_turns: Optional[int] = None,
    user_id: str = DEFAULT_EVAL_USER_ID,
    progress: bool = True,
) -> list[CaseRun]:
    """并发执行多条用例（受 Semaphore 限制，避免打满 2C2G 机器）"""
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[CaseRun] = []
    done = 0

    async def _worker(case) -> CaseRun:
        nonlocal done
        async with sem:
            if progress:
                app_logger.info(f"▶️ 开始用例 [{case.id}] {case.title}")
            res = await run_case(
                case, run_id,
                timeout_sec=timeout_sec,
                max_turns=max_turns,
                user_id=user_id,
            )
            done += 1
            if progress:
                flag = "✅" if res.status == "success" else "❌"
                app_logger.info(f"{flag} [{done}/{len(cases)}] {case.id} ({res.latency_ms / 1000:.1f}s)")
            return res

    results = await asyncio.gather(*[_worker(c) for c in cases])
    return list(results)


# ---------------------------------------------------------------- 收尾清理

def cleanup_checkpoints(run_id: str) -> int:
    """删除本次 run 在 checkpoints 表里留下的会话数据（按 thread_id 前缀）"""
    import psycopg
    from app.config import settings

    dsn = settings.database_url
    pattern = f"eval-{run_id}-%"
    deleted = 0
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM checkpoints WHERE thread_id LIKE %s", (pattern,))
                deleted += cur.rowcount or 0
            conn.commit()
    except Exception as exc:
        app_logger.warning(f"⚠️ 清理 checkpoint 失败（已忽略）: {exc}")
    return deleted


async def cleanup_eval_memory(user_id: str = DEFAULT_EVAL_USER_ID) -> None:
    """清理评测专用用户的长期记忆，保证下次评测不受上次残留影响"""
    try:
        from app.core.store import get_store

        store = await get_store()
        for namespace, key in (
            (("user_profiles", user_id), "profile"),
            (("travel_history", user_id), "history"),
        ):
            try:
                await store.adelete(namespace=namespace, key=key)
            except Exception:
                pass
        app_logger.info(f"🧹 已清理评测用户记忆: {user_id}")
    except Exception as exc:  # pragma: no cover
        app_logger.warning(f"⚠️ 清理评测记忆失败（已忽略）: {exc}")


def new_run_id() -> str:
    """生成 run id（同时作为 thread_id 前缀，便于 Langfuse 过滤与事后清理）"""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
