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


# ============== 城市码规范化 ==============
# 实测结论（2026-09-22）：VariFlight 的 depcity/arrcity 只认「城市三字码」。
# 把机场码当城市码传会直接返回「暂无数据」——北京→西安查不到就是因为传了 XIY
# （XIY 是咸阳机场码，西安的城市码是 SIA）。下面两张表做确定性纠正，
# 不再依赖模型记住对照表。

# 中文城市名 → 城市三字码
_CITY_NAME_TO_CODE = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX", "西安": "SIA",
    "成都": "CTU", "重庆": "CKG", "杭州": "HGH", "南京": "NKG", "武汉": "WUH",
    "昆明": "KMG", "三亚": "SYX", "长沙": "CSX", "郑州": "CGO", "青岛": "TAO",
    "厦门": "XMN", "天津": "TSN", "大连": "DLC", "沈阳": "SHE", "哈尔滨": "HRB",
    "乌鲁木齐": "URC", "贵阳": "KWE", "兰州": "LHW", "银川": "INC", "西宁": "XNN",
    "拉萨": "LXA", "海口": "HAK", "南宁": "NNG", "福州": "FOC", "济南": "TNA",
    "太原": "TYN", "石家庄": "SJW", "合肥": "HFE", "南昌": "KHN", "呼和浩特": "HET",
    "桂林": "KWL", "丽江": "LJG", "张家界": "DYG", "敦煌": "DNH", "喀什": "KHG",
    "温州": "WNZ", "宁波": "NGB", "无锡": "WUX", "烟台": "YNT", "珠海": "ZUH",
    "汕头": "SWA", "湛江": "ZHA", "泉州": "JJN", "常州": "CZX", "徐州": "XUZ",
    "宜昌": "YIH", "洛阳": "LYA", "义乌": "YIW", "威海": "WEH", "南通": "NTG",
}

# 机场码 → 城市码（仅纠正"多数城市两者相同"之外的特例）
_AIRPORT_CODE_TO_CITY_CODE = {
    "XIY": "SIA",   # 西安：城市 SIA / 机场 XIY  ← 北京→西安查不到的根因
    "PEK": "BJS",   # 北京首都 → 城市码 BJS
    "PKX": "BJS",   # 北京大兴 → 城市码 BJS
    "PVG": "SHA",   # 上海浦东 → 城市码 SHA
    "TFU": "CTU",   # 成都天府 → 城市码 CTU
}


def _normalize_city_code(value):
    """把中文城市名 / 误用的机场码 统一成城市三字码。"""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if not v:
        return v
    if v in _CITY_NAME_TO_CODE:
        return _CITY_NAME_TO_CODE[v]
    up = v.upper()
    if up in _AIRPORT_CODE_TO_CITY_CODE:
        return _AIRPORT_CODE_TO_CITY_CODE[up]
    return up


def _normalize_kwargs(kwargs: dict) -> dict:
    """只规范化城市类参数（*city*），不碰 dep/arr（机场码）与 date。"""
    fixed = {}
    for k, v in (kwargs or {}).items():
        if "city" in str(k).lower():
            nv = _normalize_city_code(v)
            if nv != v:
                app_logger.info(f"🔁 城市码规范化: {k} {v} -> {nv}")
            fixed[k] = nv
        else:
            fixed[k] = v
    return fixed


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
    - 识别接口的业务错误码，转成人类可读的确定结论（避免把 dict 原文丢给模型乱猜）
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

        # ---- 业务错误 / 无数据：转成确定结论，并留下原始片段便于排查 ----
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict) and inner.get("error_code"):
                code = inner.get("error_code")
                err = inner.get("error") or ""
                if str(code) == "3":
                    hint = ("城市代码格式不正确——该接口只认 IATA 城市三字码。"
                            "注意西安城市码是 SIA（不是机场码 XIY）、北京是 BJS、上海是 SHA。")
                else:
                    hint = ("该航线在指定日期暂无航班数据（可能尚未放票、无直飞或数据源未更新）；"
                            "可建议用户换一个日期，或改为高铁（需自行在 12306 购票）。")
                app_logger.warning(
                    f"✈️ 航班无数据: error_code={code}, error={err}, 原始片段={text[:300]}"
                )
                return f"Flight search results: 查询无结果（error_code={code}）。{hint}"

        flights = data.get("data") if isinstance(data, dict) else data
        if not isinstance(flights, list) or not flights:
            app_logger.warning(f"✈️ 航班返回非列表/为空，原始片段={text[:300]}")
            return raw
        if not isinstance(flights[0], dict):
            app_logger.warning(f"✈️ 航班返回结构异常，原始片段={text[:300]}")
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
    except Exception as e:
        app_logger.warning(f"✈️ 航班结果解析失败（已原样返回）: {e}; 片段={str(raw)[:300]}")
        return raw


def _wrap_with_flight_filter(orig_tool):
    """包装 searchFlightsByDepArr：规范化城市码 + 返回精简后的结果"""

    async def _inner(**kwargs):
        safe_kwargs = _normalize_kwargs(kwargs)
        raw = await orig_tool.ainvoke(safe_kwargs)
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

【可用工具】
1. 【日期与基础信息】
   - `getTodayDate`: 获取今天日期（用于用户提供相对日期时）

2. 【航班查询（核心）】
   - `searchFlightsByDepArr`: 按出发/到达城市查询航班（depcity/arrcity 必须传 IATA 城市三字码）
   - `searchFlightsByNumber`: 按航班号查询航班信息
   - `getFlightTransferInfo`: 查询中转航班信息
   - `searchFlightItineraries`: 查询可购买航班行程和最低价

【城市三字码（重要，务必按此表传参）】
- 城市码（用于 depcity/arrcity）：北京=BJS，上海=SHA，广州=CAN，深圳=SZX，
  西安=SIA，成都=CTU，重庆=CKG，杭州=HGH，南京=NKG，武汉=WUH，昆明=KMG，
  三亚=SYX，长沙=CSX，郑州=CGO，青岛=TAO，厦门=XMN，天津=TSN，大连=DLC，
  哈尔滨=HRB，乌鲁木齐=URC，贵阳=KWE，兰州=LHW，海口=HAK，南宁=NNG
- 机场码（仅用于 dep/arr）：首都机场=PEK，大兴=PKX，浦东=PVG，虹桥=SHA，咸阳=XIY
- ⚠️ 最容易错的两个：西安【城市码是 SIA，不是 XIY（XIY 只是咸阳机场）】；
  上海【城市码 SHA，机场是 PVG/SHA】。城市码传成机场码会直接查不到任何航班。

【工作流程】
1. 分析用户查询，提取出发地、目的地、日期
2. 如果用户说"明天"等相对日期，先调用getTodayDate获取今天日期
3. 查城市对时用 depcity/arrcity（城市码）；查具体机场时才用 dep/arr（机场码）
4. 日期格式必须是 YYYY-MM-DD

【输出规则（必须严格遵守，目的是节省 token）】
1. 工具返回的航班清单已经过系统精选（⭐为准点率100%航班，已按准点率从高到低排列）。
2. 最多展示 8 个航班：先展示 ⭐ 航班，并明确标注「优先推荐航班准点率100%航线」；⭐ 不够 8 个再按准点率顺延补齐。
3. 严禁罗列全部航班，严禁把同一份清单重复输出两遍；其余航班只用一句话概括总数。
4. 单个航班只保留：航班号、航司、出发/到达机场与时间、准点率；价格只有调用 searchFlightItineraries 拿到后才展示，没有就写"暂无报价"。
5. 已取消航班已被系统剔除，无需再提醒。
6. 如果工具返回"查询无结果/暂无数据"，如实说明该航线在该日期暂无航班数据，并给出两个具体出口：
   ① 换一个日期再查；② 改乘高铁（票务需用户自行在 12306 购买，本系统不提供车次查询）。
   禁止编造航班号、时刻与票价，也禁止承诺"帮你查车次"。

【注意】
- 一定要调用工具，不要编造数据
- 如果没找到航班，明确告知用户，不要反复重试同一个无效参数
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