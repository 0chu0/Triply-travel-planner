
"""
MCP 工具筛选器
按能力标签（capability）聚合特定类型的 MCP 工具；日期工具为本地确定性工具，不依赖 MCP。

设计说明（v1.5）：
- 旧实现：对全量工具名做子串匹配（如 'searchhotels' / 'gettodaydate'）。
  脆弱点：外部服务一旦改名/换供应商，筛选结果会静默变成空列表，且无任何报错。
- 新实现：每个 MCP 服务在 client.SERVER_CONFIGS 中声明 capability 标签，
  get_tools_by_capability(cap) 直接按标签聚合，工具名变化不影响聚合结果。
- 日期能力从 VariFlight 的 getTodayDate 迁到本地 datetime 工具（见 date_tools.py）。
"""

from typing import List
from langchain_core.tools import BaseTool
from app.mcp_core.client import get_mcp_client
from app.utils.logger import app_logger
from app.tools.date_tools import get_today_date


async def get_all_mcp_tools() -> List[BaseTool]:
    """获取所有 MCP 工具"""
    manager = await get_mcp_client()
    tools = await manager.get_tools()
    app_logger.info(f"📦 获取了 {len(tools)} 个 MCP 工具")
    return tools


async def _by_capability(capability: str) -> List[BaseTool]:
    """按能力标签从已加载的 MCP 服务聚合工具（不再对工具名做子串匹配）"""
    manager = await get_mcp_client()
    tools = manager.get_tools_by_capability(capability)
    app_logger.info(f"🔧 能力[{capability}]工具: {[t.name for t in tools]}")
    return tools


async def get_hotel_tools() -> List[BaseTool]:
    """
    获取酒店相关工具（来自 aigohotel 服务的 hotel 能力）
    """
    return await _by_capability("hotel")


async def get_weather_tools() -> List[BaseTool]:
    """
    获取天气相关工具（来自 weather 服务的 weather 能力）
    """
    return await _by_capability("weather")


async def get_search_tools() -> List[BaseTool]:
    """
    获取搜索相关工具（来自 search 服务的 search 能力）
    """
    return await _by_capability("search")


async def get_map_poi_tools() -> List[BaseTool]:
    """
    获取地图/周边 POI 相关工具（来自 amap 服务的 map_poi 能力）。
    用于目的地推荐、住宿规划中“查周边餐厅/地铁/景点”等场景（原 hotel_tools 内含的 maps_around_search）。
    """
    return await _by_capability("map_poi")


async def get_date_tools() -> List[BaseTool]:
    """
    获取日期工具：本地确定性实现（datetime），不依赖任何外部 MCP 服务。

    彻底解耦对航班服务(VariFlight) getTodayDate 的依赖——
    航班服务挂掉或 key 失效时，“今天几号”能力依然可用。
    """
    tools = [get_today_date]
    app_logger.info(f"📅 日期工具(本地): {[t.name for t in tools]}")
    return tools
