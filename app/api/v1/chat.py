
"""
流式对话 API（SSE）
"""
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse #流式返回
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from app.models.base import get_db, async_session_maker
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.usage import QuotaRequest
from app.schemas.message import MessageCreate
from app.api.dependencies import get_current_user
from app.agents.handoffs.travel_agent import create_travel_agent
from app.utils.logger import app_logger
from app.utils.quota import (
    get_or_create_usage,
    build_usage_payload,
    record_usage,
    estimate_tokens,
)
from app.core.tracing import get_langfuse_handler, flush_langfuse

router = APIRouter(prefix="/chat", tags=["对话"])


async def save_message(
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict = None
) -> Message:
    """保存消息到数据库"""

    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        extra_info=metadata or {}
    )

    db.add(message)

    # ========== 顺手刷新会话的 updated_at ==========
    # 列表接口按 updated_at DESC 排序（conversations.py），前端也拿它当"最近活动时间"显示。
    # Conversation.updated_at 上虽然写了 onupdate=func.now()，但 SQLAlchemy 的 onupdate
    # 只在【该 ORM 对象自身被 UPDATE】时才触发 —— 这里只 add Message、压根没碰 Conversation 实例，
    # 所以聊天永远不会触发它，会话位置会一直停在"创建时间"那个点（2026-10-10 修复）。
    # ⚠️ 必须用 clock_timestamp() 而不是 now()：PG 的 now() 返回的是【事务开始时间】
    # （transaction_timestamp），不是语句执行时间。而本函数末尾的 db.refresh(message) 会顺手
    # 开启一个新事务且一直挂着不提交 —— 于是下一次 save_message 的 UPDATE 就跑在那个旧事务里，
    # now() 拿到的是几秒前的时刻，刷新直接失效（2026-10-10 实测：两次保存相隔 3 秒，
    # 两条 UPDATE 的 SQL 都发了，updated_at 却一模一样）。clock_timestamp() 取真实时钟，不受事务影响。
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.clock_timestamp())
    )

    await db.commit()
    await db.refresh(message)

    return message


def sse(data: dict) -> str:
    """
    SSE 标准 data 帧
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 主图中"负责写回答"的 LLM 节点名。
# langchain 的 create_agent 固定把模型节点注册为 "model"（见 create_agent 源码
# graph.add_node("model", ...)），只有它的输出才是给用户看的正文。
ANSWER_STREAM_NODES = frozenset({"model"})


def is_answer_stream_event(event: dict) -> bool:
    """
    判断一个 chat model 流式事件是否属于「给用户的正文」。

    为什么需要这个判断：
        agent.astream_events() 遍历的是【整棵运行树】，不只是主图。工具内部
        嵌套的子图（目的地 Router 的 classifier / explore / weather）和工具里
        直连的 LLM（RAG 的查询改写）都会同样发出 on_chat_model_stream 事件。
        它们的内容是内部数据，例如分类器返回的
            {"classifications": [{"agent": "explore", ...}]}
        若不加区分地转发，就会泄漏到聊天正文里，还会被当成 assistant 回复存库。

    判定依据一（节点名）：
        langgraph 运行每个节点时会往事件 metadata 写入 "langgraph_node"；
        metadata 合并是新值覆盖旧值（patch_config → _merge_metadata），
        因此子图节点的名字会盖掉外层的 "tools"，可以精确区分来源。

    判定依据二（命名空间层级，2026-10-10 修复）：
        子图也用 create_agent 构建，模型节点同样叫 "model"，只靠节点名无法区分。
        真正的分界是 checkpoint_ns 的层级：
            主图   "model:63d08332-…"                        单层，无 "|"
            子图   "tools:xxx|explore:yyy|model:zzz"          多层，含 "|"
        destination_router 调 _explore_agent.ainvoke() 时未传 config，子图的流式
        事件会一路冒泡到外层 astream_events，节点名又恰好命中白名单，于是内部
        攻略（带 ### / * 的景点清单）被当成正文发给用户、并写进 messages 入库，
        下一轮又被当历史重放 —— 既污染体验也推高输入成本。
        实测一条用例可混入 633 条子图片段（见 scripts/eval_out 的 subgraph_leak）。
    """
    if event.get("event") != "on_chat_model_stream":
        return False
    metadata = event.get("metadata") or {}
    if metadata.get("langgraph_node") not in ANSWER_STREAM_NODES:
        return False
    # 含 "|" = 来自嵌套子图，不是主图给用户的正文
    ns = str(
        metadata.get("langgraph_checkpoint_ns")
        or metadata.get("checkpoint_ns")
        or ""
    )
    return "|" not in ns


async def get_state_message_count(agent, conversation_id: str) -> int:
    """读取本轮开始前，图状态里已有的消息条数（用于兜底时划定"本轮新增"边界）。"""
    try:
        state = await agent.aget_state(
            {"configurable": {"thread_id": conversation_id}}
        )
        values = getattr(state, "values", None) or {}
        return len(values.get("messages") or [])
    except Exception as e:
        app_logger.warning(f"⚠️ 读取图状态消息条数失败（已忽略）: {e}")
        return 0


async def recover_final_answer(agent, conversation_id: str, min_index: int = 0) -> str:
    """
    兜底：从图的最新状态里取最后一条 AI 消息。

    白名单过滤的唯一风险是「误杀」——将来 langchain 若改了节点命名，正文会被
    静默丢弃，用户看到空白回复（比泄漏更糟）。所以一轮结束时如果一个字都没流
    出来，就用图的最终状态补发一次完整回答。

    min_index 是本轮开始前的消息条数：只接受本轮新增的消息。否则当本轮压根没
    生成回答（例如停在需要审批的中断处）时，会把上一轮的回答重复发一遍。
    """
    try:
        state = await agent.aget_state(
            {"configurable": {"thread_id": conversation_id}}
        )
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") or []
        for message in reversed(messages[min_index:]):
            if not isinstance(message, AIMessage):
                continue
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    except Exception as e:
        app_logger.warning(f"⚠️ 兜底读取最终回答失败（已忽略）: {e}")
    return ""


async def generate_sse_stream(
        conversation_id: str,
        user_message: str,
        db: AsyncSession,
        user: User
):
    assistant_message = ""

    # token 统计（含 Agent 内部的多次 LLM 调用，如工具调用循环）
    prompt_tokens = 0
    completion_tokens = 0

    try:
        # 1. 保存用户消息
        await save_message(db, conversation_id, "user", user_message)

        # 2. 创建 agent
        agent = await create_travel_agent()

        # 3. 关键修复：输入必须是字典格式！
        # LangGraph StateGraph 期望输入是 state 的部分更新
        input_data = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": str(user.id),
        }

        streamed_answer = False
        ignored_nodes = set()

        # 记下本轮开始前的消息条数，兜底时只认本轮新增的消息
        turn_start_index = await get_state_message_count(agent, conversation_id)

        # 4. 使用 astream_events 获取更细粒度的流式输出
        #    注入 Langfuse 链路追踪：未配置时 handler 为 None，自动跳过，不影响主流程
        langfuse_handler = get_langfuse_handler()
        run_config = {"configurable": {"thread_id": conversation_id}}
        if langfuse_handler is not None:
            run_config["callbacks"] = [langfuse_handler]

        async for event in agent.astream_events(
                input_data,
                config=run_config,
                version="v2"
        ):
            kind = event.get("event")

            # 只转发主图 model 节点的流式输出（= 给用户的正文）
            if kind == "on_chat_model_stream":
                if not is_answer_stream_event(event):
                    node = (event.get("metadata") or {}).get("langgraph_node")
                    if node not in ignored_nodes:
                        ignored_nodes.add(node)
                        app_logger.info(
                            f"⏭️ 内部节点的流式输出不进正文: node={node}"
                        )
                    await asyncio.sleep(0)
                    continue

                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    token = chunk.content
                    # 部分模型会把 content 返回成列表（多模态分片），只取文本
                    if not isinstance(token, str):
                        token = "".join(
                            part.get("text", "") if isinstance(part, dict) else str(part)
                            for part in token
                        )
                    if token:
                        streamed_answer = True
                        assistant_message += token
                        yield sse({
                            "type": "token",
                            "content": token,
                        })

            # 每次 LLM 调用结束：累计官方返回的 token 用量
            elif kind == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                usage_meta = (
                    output.get("usage_metadata")
                    if isinstance(output, dict)
                    else getattr(output, "usage_metadata", None)
                )
                if usage_meta:
                    prompt_tokens += int(usage_meta.get("input_tokens") or 0)
                    completion_tokens += int(usage_meta.get("output_tokens") or 0)

            # 或者捕获工具调用信息
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                yield sse({
                    "type": "tool_call",
                    "tool": tool_name,
                })

            await asyncio.sleep(0)

        # 4.1 请求结束，刷新 Langfuse 上报队列（失败不影响主流程）
        try:
            flush_langfuse()
        except Exception:
            pass

        # 5. 兜底：白名单若因框架升级误杀，会变成空白回复（比泄漏更糟），
        #    此时用图的最终状态补发一次完整回答。
        if not streamed_answer:
            recovered = await recover_final_answer(
                agent, conversation_id, turn_start_index
            )
            if recovered:
                app_logger.warning("⚠️ 未捕获到流式正文，已用最终状态兜底补发")
                assistant_message = recovered
                yield sse({"type": "token", "content": recovered})

        # 5.1 最后一道防线：兜底也拿不到内容时，绝不能让用户看到空气泡。
        #     正常流程不应触发；一旦触发说明模型本轮没产出正文，用一个明确提示
        #     代替空白，同时留下日志便于定位。
        if not assistant_message.strip():
            notice = "抱歉，这次没有生成有效内容。请再发一次，或换个说法试试。"
            app_logger.warning("⚠️ 本轮模型未产出任何正文，已补发兜底提示")
            assistant_message = notice
            yield sse({"type": "token", "content": notice})

        # 6. 保存 AI 回复
        if assistant_message.strip():
            await save_message(
                db,
                conversation_id,
                "assistant",
                assistant_message,
            )

        # 7. 结算 token 用量
        #    模型未返回 usage 时用字符数兜底估算，避免出现「永远不扣额度」的漏计
        if prompt_tokens == 0 and completion_tokens == 0:
            prompt_tokens = estimate_tokens(user_message)
            completion_tokens = estimate_tokens(assistant_message)

        usage_payload = None
        try:
            async with async_session_maker() as usage_db:
                usage = await record_usage(
                    usage_db, user.id, prompt_tokens, completion_tokens
                )
                if usage is not None:
                    usage_payload = build_usage_payload(usage)
        except Exception as e:
            app_logger.warning(f"⚠️ token 用量结算失败（已忽略）: {e}")

        if usage_payload:
            yield sse({"type": "usage", **usage_payload})

        yield sse({"type": "done"})

    except Exception as e:
        app_logger.exception("❌ SSE 流式对话错误")
        yield sse({
            "type": "error",
            "message": str(e),
        })



@router.post("/stream/{conversation_id}")
async def stream_chat(
        conversation_id: str,
        data: MessageCreate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    流式对话（SSE）

    Returns:
        StreamingResponse: SSE 流式响应
    """

    # 验证会话归属
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )

    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    # 配额硬拦截：额度用尽 → 402，前端据此展示「额度不足 + 申请入口」
    usage = await get_or_create_usage(db, user.id)
    await db.commit()

    # 顺手查一下有没有待处理申请，让 402 payload 里的 has_pending_request
    # 也带上正确状态，前端的申请按钮就能正确显示「申请已提交」并置灰
    pending_row = await db.execute(
        select(QuotaRequest.id)
        .where(QuotaRequest.user_id == user.id)
        .where(QuotaRequest.status == "pending")
        .limit(1)
    )
    has_pending = pending_row.first() is not None
    payload = build_usage_payload(usage, has_pending_request=has_pending)

    if payload["exceeded"]:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "QUOTA_EXCEEDED",
                "message": "免费体验额度已用尽，服务已暂停。可提交申请获取更多额度。",
                **payload,
            }
        )

    # 返回 SSE 流
    return StreamingResponse(
        generate_sse_stream(conversation_id, data.content, db, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲
        }
    )


@router.get("/history/{conversation_id}")
async def get_chat_history(
        conversation_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """获取会话历史消息"""

    # 验证会话归属
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )

    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    # 查询消息
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )

    messages = result.scalars().all()

    return {
        "conversation": conversation.to_dict(),
        "messages": [m.to_dict() for m in messages]
    }