"""
交通查询工具
调用交通规划协调器（Subagents 主 Agent）
"""
from langchain.tools import tool
from app.agents.subagents.transport_coordinator import create_transport_coordinator
from app.utils.logger import app_logger

# 铁路/高铁说明（本项目没有接入 12306，不做车次查询，只做引导）
_RAIL_NOTICE = (
    "【铁路/高铁说明】本项目未接入 12306 车次查询能力，无法查询具体车次、票价与余票。"
    "请用户自行在 12306（或第三方购票平台）购票。"
    "可以给出的帮助：出发/到达站的大致选择建议（如北京西 → 西安北）、"
    "建议的车次时段（上午出发中午到、傍晚返程）、以及高铁与航班/自驾的对比结论。"
    "禁止编造或承诺任何具体车次号、发车时刻与票价。"
)


@tool
async def query_transport_options(
    origin_city: str,
    destination_city: str,
    departure_date: str,
    transport_type: str = None
) -> str:
    """
    查询交通选项（调用交通规划协调器）

    参数说明：
    - origin_city: 出发城市
    - destination_city: 目的地城市
    - departure_date: 出发日期，格式 YYYY-MM-DD
    - transport_type: 交通方式（可选），可选值：
      * flight（航班）
      * driving（自驾）
      * rail（铁路/高铁）—— 本项目不查车次，直接返回购票引导说明

    返回：
    - 格式化的交通选项信息
    """

    # 铁路/高铁：没有对应数据源，直接返回确定性的引导文案，
    # 既不会编造车次，也不会让流程卡在交通步骤。
    if transport_type == "rail":
        app_logger.info(f"🔧 铁路/高铁（无车次查询能力，返回引导说明）: {origin_city} -> {destination_city}")
        return (
            f"{origin_city} → {destination_city}（{departure_date}）\n"
            f"{_RAIL_NOTICE}"
        )

    app_logger.info(f"🔧 调用交通规划协调器")

    # 异步创建协调器（主 Agent）
    coordinator = await create_transport_coordinator()

    # 构建用户查询
    if transport_type:
        type_labels = {
            "flight": "航班",
            "driving": "自驾",
            "rail": "铁路/高铁"
        }
        user_query = (
            f"我想从 {origin_city} 去 {destination_city}，"
            f"出发日期是 {departure_date}，"
            f"交通方式选择 {type_labels.get(transport_type, transport_type)}，"
            f"请帮我查询详细信息。"
        )
    else:
        user_query = (
            f"我想从 {origin_city} 去 {destination_city}，"
            f"出发日期是 {departure_date}，"
            f"请推荐合适的交通方式并提供详细信息。"
        )

    # 调用协调器
    result = await coordinator.ainvoke({
        "messages": [
            {"role": "user", "content": user_query}
        ]
    })

    return result["messages"][-1].content