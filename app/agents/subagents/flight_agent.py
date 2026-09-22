"""
航班查询 Subagent
调用 Aviation MCP 的多个工具
"""
import asyncio
import ast
import re
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from app.config import settings
from app.mcp_core.client import get_mcp_client
from app.utils.logger import app_logger


# ============== 航班结果精简过滤（省 token + 准点率100%优先） ==============

_MAX_FLIGHTS = 12


def _parse_rate(value) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return -1.0


def _hhmm(value) -> str:
    m = re.search(r"(\d{2}:\d{2})", str(value or ""))
    return m.group(1) if m else ""


def _filter_flight_result(raw):
    """
    对 searchFlightsByDepArr 原始返回做确定性精简：
    - 解析 Python dict repr（该 MCP 返回的不是 JSON）
    - 剔除已取消航班
    - 按准点率从高到低排序，准点率100% 优先并标 ⭐
    - 精简字段 + 截断至 _MAX_FLIGHTS 条，开头标注「优先推荐航班准点率100%航线」
    解析失败时原样返回，绝不影响可用性。
    """
    try:
        if isinstance(raw, list):
            text = "".join(
                b.get("text", "") for b in raw if isinstance(b, dict)
            )
        else:
            text = str(raw)

        marker = "Flight search results:"
        idx = text.find(marker)
        payload = text[idx + len(marker):].strip() if idx >= 0 else text.strip()
        data = ast.literal_eval(payload)
        flights = data.get("data") if isinstance(data, dict) else data
        if not isinstance(flights, list) or not flights:
            return raw
        if not isinstance(flights[0], dict):
            return raw

        total = len(flights)
        active = [f for f in flights if "取消" not in str(f.get("FlightState", ""))]
        cancelled = total - len(active)
        active.sort(key=lambda f: _parse_rate(f.get("OntimeRate")), reverse=True)
        picked = active[:_MAX_FLIGHTS]

        lines = [
            f"Flight search results: 共{total}条，已剔除取消航班{cancelled}条，"
            f"按准点率从高到低精选{len(picked)}条。"
            f"优先推荐航班准点率100%航线（标⭐的为准点率100%）："
        ]
        for f in picked:
            rate = _parse_rate(f.get("OntimeRate"))
            star = "⭐" if rate >= 100 else "·"
            lines.append(
                f"{star} {f.get('FlightNo', '')} {f.get('FlightCompany', '')} | "
                f"{f.get('FlightDep', '')}{f.get('FlightDepAirport', '')} {_hhmm(f.get('FlightDeptimePlanDate'))} → "
                f"{f.get('FlightArr', '')}{f.get('FlightArrAirport', '')} {_hhmm(f.get('FlightArrtimePlanDate'))} | "
                f"准点率{f.get('OntimeRate', '')} | {f.get('FlightState', '')}"
            )
        return "\n".join(lines)
    except Exception:
        return raw


def _wrap_with_flight_filter(orig_tool):
    """包装 searchFlightsByDepArr，返回精简后的结果"""

    async def _inner(**kwargs):
        raw = await orig_tool.ainvoke(kwargs)
        filtered = _filter_flight_result(raw)
        app_logger.info(
            f"✈️ 航班结果已精简: {len(str(raw))} -> {len(str(filtered))} 字符"
        )
        return filtered

    return StructuredTool.from_function(
        name=orig_tool.name,
        description=orig_tool.description,
        args_schema=orig_tool.args_schema,
        coroutine=_inner,
    )


async def _get_aviation_tools():
    """获取航班相关的MCP工具"""
    manager = await get_mcp_client()
    all_tools = await manager.get_tools()

    # 筛选航班工具
    aviation_tools = [
        tool for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in [
            'flight', 'aviation', 'searchflights', 'gettodaydate'
        ])
    ]

    # 对大结果集工具包一层精简过滤（准点率100%优先 + 截断，大幅节省 token）
    aviation_tools = [
        _wrap_with_flight_filter(t) if t.name == "searchFlightsByDepArr" else t
        for t in aviation_tools
    ]

    app_logger.info(f"✈️ 航班工具: {[t.name for t in aviation_tools]}")
    return aviation_tools


async def create_flight_subagent():
    """创建航班查询 Subagent"""

    llm = ChatOpenAI(
        model=settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=0.1,
        extra_body={"enable_thinking": False, "enable_context_cache": True}
    )

    # 异步获取工具
    aviation_tools = await _get_aviation_tools()

    agent = create_agent(
        model=llm,
        tools=aviation_tools,
        system_prompt="""你是航班查询专家，负责处理航班查询、机票价格比较及航班状态查询。可以使用以下工具：

**可用工具**：
1.  **日期与基础信息**：
    - `getTodayDate`: 获取今天日期（用于用户提供相对日期时）

2.  **航班查询（核心）**：
    - `searchFlightsByDepArr`: 按出发/到达城市查询航班（需IATA三字码）
    - `searchFlightsByNumber`: 按航班号查询航班信息
    - `getFlightTransferInfo`: 查询中转航班信息
    - `searchFlightItineraries`: 查询可购买航班行程和最低价

**IATA三字码示例**：
- 城市码：北京=BJS, 上海=SHA, 广州=CAN, 西安=XIY, 成都=CTU
- 机场码：首都机场=PEK, 浦东=PVG, 虹桥=SHA

**工作流程**：
1. 分析用户查询，提取出发地、目的地、日期
2. 如果用户说"明天"等相对日期，先调用getTodayDate获取今天日期
3. 如果查询城市有多个机场，使用depcity/arrcity参数
4. 如果查询具体机场，使用dep/arr参数

**输出规则（必须严格遵守，目的是节省 token）**：
1. 工具返回的航班清单已经过系统精选（⭐为准点率100%航班，已按准点率从高到低排列）。
2. 最多展示 8 个航班：先展示 ⭐ 航班，并明确标注「优先推荐航班准点率100%航线」；⭐ 不够 8 个再按准点率顺延补齐。
3. 严禁罗列全部航班，严禁把同一份清单重复输出两遍；其余航班只用一句话概括总数。
4. 单个航班只保留：航班号、航司、出发/到达机场与时间、准点率；价格只有调用 searchFlightItineraries 拿到后才展示，没有就写"暂无报价"。
5. 已取消航班已被系统剔除，无需再提醒。

**注意**：
- 一定要调用工具，不要编造数据
- 日期格式必须是YYYY-MM-DD
- 如果没找到航班，明确告知用户
"""
    )

    app_logger.info("✅ 航班 Subagent 创建完成")
    return agent


if __name__ == "__main__":
    async def main():
        print("\n" + "=" * 50)
        print("🚀 正在初始化航班查询 Subagent...")
        print("=" * 50)

        flight_agent = await create_flight_subagent()

        test_query = "帮我查一下明天从北京到上海的航班"

        print(f"\n❓ 用户提问: {test_query}")
        print("-" * 30)

        response = await flight_agent.ainvoke({
            "messages": [{"role": "user", "content": test_query}]
        })

        print("-" * 30)
        print("✅ Agent 回复:")
        final_message = response["messages"][-1].content
        print(final_message)

        print("\n" + "=" * 50)
        print("测试结束")
        print("=" * 50)


    asyncio.run(main())