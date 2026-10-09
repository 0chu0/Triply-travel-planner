"""
Handoffs 主 Agent
一个 Agent + 中间件实现整个旅行规划流程
"""
from app.tools.mcp_tools import get_all_mcp_tools
from app.tools.router_query import query_destination_info
from app.tools.transport_query import query_transport_options
from app.tools.date_tools import get_today_date
from langchain.agents import create_agent
from app.config import settings
from app.core.state import TravelState
from app.core.checkpointer import get_checkpointer
from app.core.llm import get_main_llm
from app.core.middleware import create_step_config_middleware
from app.tools.state_transition import (
    record_requirement_tool,
    select_destination_tool,
    select_transport_tool,
    select_accommodation_tool,
    select_food_tool,
    generate_itinerary_tool,
    summarize_budget_tool,
    generate_order_tool,
    ALL_ROLLBACK_TOOLS
)
from app.tools.memory_tools import MEMORY_TOOLS
from app.utils.logger import app_logger

# ============== 初始化 LLM ==============
# 真正的构造逻辑收敛到 app/core/llm.py：全项目只有一处定义"模型档位 → 模型名"，
# 避免某处硬编码模型名后、该模型免费额度用尽时全链路 403 却极难排查。
# 主 Agent 本身用 main 档；每轮实际用哪个档位由 StepConfigMiddleware
# 按 step_config["model_tier"] 动态 override（见 app/core/middleware.py）。

def get_llm():
    """获取配置好的千问模型（main 档）"""
    return get_main_llm()


# ============== 创建 Agent ==============

async def create_travel_agent():
    """
    创建 Handoffs 旅行规划 Agent

    返回：
        编译好的 Agent（可直接调用）
    """

    app_logger.info("创建 Travel Agent（带审批）...")

    llm = get_llm()
    all_mcp_tools = await get_all_mcp_tools()

    # 异步创建中间件（预加载配置）
    step_config_middleware = await create_step_config_middleware()

    all_tools = [
        record_requirement_tool,
        select_destination_tool,
        select_transport_tool,
        select_accommodation_tool,
        select_food_tool,
        generate_itinerary_tool,
        summarize_budget_tool,
        generate_order_tool,
        *ALL_ROLLBACK_TOOLS,
        query_destination_info,
        query_transport_options,
        # 本地日期工具：必须在此登记。step_config 会把它注入 request.tools，
        # 而 create_agent 只允许中间件使用"创建时已登记"的工具（否则报
        # Middleware added tools that the agent doesn't know how to execute）。
        # v1.5 把日期能力从 VariFlight 的 getTodayDate(MCP) 迁到本地工具后
        # 曾漏登记，导致需求收集步骤一被注入就抛错。
        get_today_date,
        *all_mcp_tools,
        *MEMORY_TOOLS,
    ]

    agent = create_agent(
        model=llm,
        tools=all_tools,
        state_schema=TravelState,
        middleware=[step_config_middleware],  # 使用预加载的中间件
        checkpointer=await get_checkpointer(),
    )

    app_logger.info("✅ Travel Agent（带审批）创建完成")

    return agent