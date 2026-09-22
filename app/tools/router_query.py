"""
Router 查询工具
"""
import re
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langchain.tools import ToolRuntime
from app.agents.routers.destination_router import create_destination_router
from app.utils.logger import app_logger


@tool
async def query_destination_info(
        destination: str,
        query: str = "",
        runtime: ToolRuntime = None
) -> Command:
    """
    查询目的地详细信息（并行查询多个源）

    此工具会调用 Router，并行执行：
    1. 探索 Agent：从 RAG 系统检索景点攻略
    2. 天气 Agent：查询实时天气信息（高德真实数据）

    参数：
    - destination: 目的地名称，如 "西安"
    - query: 具体查询（可选），如 "景点推荐"

    返回：
    - 综合的目的地信息（景点 + 天气），并把实时天气单独沉淀到状态中，
      供后续步骤（交通/住宿/餐饮/行程）直接引用，避免跨步骤丢失。
    """

    app_logger.info(f"调用目的地 Router: {destination}")

    # 创建 Router
    router = create_destination_router()

    # 如果没有提供具体查询，使用默认
    if not query:
        query = f"推荐{destination}旅游"

    # 调用 Router
    result = await router.ainvoke({
        "original_query": query,
        "destination": destination
    })

    final_report = result["final_report"]

    # 抽取天气片段（天气章节通常是报告的最后一个 "## xxx 天气信息" 段落）
    weather_block = ""
    m = re.search(r"(##\s*.*?天气信息.*)$", final_report, re.DOTALL)
    if m:
        weather_block = m.group(1).strip()

    app_logger.info(
        f"目的地 Router 完成: {destination}，天气片段长度={len(weather_block)}"
    )

    # 把天气沉淀到状态；同时把综合报告作为工具消息返回给模型
    tool_messages = []
    if runtime is not None:
        tool_messages.append(
            ToolMessage(content=final_report, tool_call_id=runtime.tool_call_id)
        )

    return Command(update={
        "messages": tool_messages,
        "destination_weather": weather_block,
    })
