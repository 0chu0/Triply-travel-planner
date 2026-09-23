
"""
流式对话 API（SSE）
"""
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse #流式返回
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from app.models.base import get_db, async_session_maker
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
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

    判定依据：
        langgraph 运行每个节点时会往事件 metadata 写入 "langgraph_node"；
        metadata 合并是新值覆盖旧值（patch_config → _merge_metadata），
        因此子图节点的名字会盖掉外层的 "tools"，可以精确区分来源。
    """
    if event.get("event") != "on_chat_model_stream":
        return False
    metadata = event.get("metadata") or {}
    return metadata.get("langgraph_node") in ANSWER_STREAM_NODES


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
        async for event in agent.astream_events(
                input_data,
                config={
                    "configurable": {
                        "thread_id": conversation_id
                    }
                },
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
    payload = build_usage_payload(usage)

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