#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/7 16:16
@Author  : GGBOND
@File    : transport_coordinator.py
@Software: PyCharm
"""
"""
交通规划协调器（主 Agent）
整合三个 Subagents + 辅助工具
"""
import asyncio
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.agents.subagents.flight_agent import create_flight_subagent
from app.agents.subagents.driving_agent import create_driving_subagent
from app.mcp_core.client import get_mcp_client
from app.utils.logger import app_logger


# ============== 获取辅助工具 ==============

async def _get_auxiliary_tools():
    """获取辅助工具"""
    manager = await get_mcp_client()
    all_tools = await manager.get_tools()

    # 筛选辅助工具
    aux_tools = [
        tool for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in [
            'getfutureweather', 'get-current-date',
            'maps_around_search'
        ])
    ]

    app_logger.info(f"🛠️ 辅助工具: {[t.name for t in aux_tools]}")
    return aux_tools


# ============== 创建交通规划主 Agent ==============

async def create_transport_coordinator():
    """
    创建交通规划协调器（主 Agent）

    整合：
    - 2个子Agent工具（航班、自驾）
    - MCP辅助工具（酒店、天气、日期、周边搜索）
    """

    llm = ChatOpenAI(
        model=settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=0.7,
        extra_body={"enable_thinking": False, "enable_context_cache": True}
    )

    # 异步创建子Agents
    flight_subagent = await create_flight_subagent()
    driving_subagent = await create_driving_subagent()

    # ============== 将 Subagents 包装为 Tools ==============

    @tool("query_flights", description="查询航班信息。需要提供出发城市、目的地城市、出发日期。")
    async def query_flights_tool(
        origin: str,
        destination: str,
        departure_date: str
    ) -> str:
        """查询航班信息（调用航班 Subagent）"""

        app_logger.info(f"🔧 调用航班 Subagent: {origin} -> {destination}")

        result = await flight_subagent.ainvoke({
            "messages": [
                {
                    "role": "user",
                    "content": f"请查询从 {origin} 到 {destination} 的航班，出发日期是 {departure_date}。"
                }
            ]
        })

        return result["messages"][-1].content

    @tool("plan_driving_route", description="规划自驾路线。需要提供出发地和目的地（地址或地名）。")
    async def plan_driving_route_tool(
        origin: str,
        destination: str
    ) -> str:
        """规划自驾路线（调用自驾 Subagent）"""

        app_logger.info(f"🔧 调用自驾 Subagent: {origin} -> {destination}")

        result = await driving_subagent.ainvoke({
            "messages": [
                {
                    "role": "user",
                    "content": f"请规划从 {origin} 到 {destination} 的自驾路线。"
                }
            ]
        })

        return result["messages"][-1].content

    # 获取辅助工具
    auxiliary_tools = await _get_auxiliary_tools()

    # 组合所有工具
    all_tools = [
        query_flights_tool,
        plan_driving_route_tool,
    ] + auxiliary_tools

    coordinator = create_agent(
        model=llm,
        tools=all_tools,
        system_prompt="""你是交通规划协调专家。

**可用工具**：

**交通查询（主要）**：
1. query_flights：查询航班信息（适合长途，速度快）
2. plan_driving_route：规划自驾路线（适合深度游，自由灵活）

**辅助工具（按需使用）**：
- getFutureWeatherByAirport：查询机场未来天气(现在不能使用,不准调用这个工具)
- get-current-date：获取今天日期（调用需要时间的工具前,获取实时时间）

**工作流程**：
1. 理解用户的交通需求（出发地、目的地、日期、人数）
2. 如果用户明确指定交通方式，直接调用对应工具
3. 如果用户未指定，根据距离推荐：
   * < 300km：推荐自驾
   * 300-1000km：推荐航班或自驾
   * > 1000km：推荐航班
4. 调用工具后，用清晰格式展示结果
5. 可以主动询问用户偏好（时间优先还是价格优先）

**注意事项**：
- 用户说的今天和明天之类的词要以get-current-date获取的时间为标准
- 一定要调用工具获取实时信息，不要编造数据
- 如果查询失败，告知用户并提供替代方案
- 航班需要提供日期，自驾不需要
- 调用工具前，先调用get-current-date获取今天日期
- query_flights 返回的清单已是系统精选结果（⭐=准点率100%，标注「优先推荐航班准点率100%航线」）：直接沿用该精选清单展示，最多 8 条，禁止重新罗列更长的清单，禁止把同一份清单输出两遍，已取消航班无需再提
"""
    )

    app_logger.info("✅ 交通规划协调器（主 Agent）创建完成")

    return coordinator


if __name__ == "__main__":
    async def main():
        print("\n" + "=" * 50)
        print("🚀 正在初始化交通规划协调器...")
        print("=" * 50)

        coordinator = await create_transport_coordinator()

        test_query = "我想从北京去上海，明天出发，帮我推荐交通方式"

        print(f"\n❓ 用户提问: {test_query}")
        print("-" * 30)

        response = await coordinator.ainvoke({
            "messages": [{"role": "user", "content": test_query}]
        })

        print("-" * 30)
        print("✅ 协调器回复:")
        final_message = response["messages"][-1].content
        print(final_message)

        print("\n" + "=" * 50)
        print("测试结束")
        print("=" * 50)

    asyncio.run(main())