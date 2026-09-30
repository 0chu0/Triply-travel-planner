"""
天气服务 MCP Server
使用高德天气 API 查询天气预报
"""
import os
import json
import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("weather-service")

AMAP_API_KEY = os.getenv("AMAP_API_KEY")
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
AMAP_GEO_URL = "https://restapi.amap.com/v3/geocode/geo"


# 常见城市名 -> 高德 6 位 adcode 速查表。
# 高德 geo（地理编码）接口在免费额度下极易被限流，且对"北京/西安"等
# 直辖市/省会裸名时好时坏；本地表优先匹配，稳定、零配额、不限流。
# 数据来源：高德开放平台行政区划 adcode。
CITY_ADCODE_MAP = {
    "北京": "110000", "北京市": "110000",
    "上海": "310000", "上海市": "310000",
    "天津": "120000", "天津市": "120000",
    "重庆": "500000", "重庆市": "500000",
    "广州": "440100", "深圳": "440300",
    "成都": "510100", "杭州": "330100",
    "南京": "320100", "武汉": "420100",
    "西安": "610100", "苏州": "320500",
    "郑州": "410100", "长沙": "430100",
    "沈阳": "210100", "青岛": "370200",
    "大连": "210200", "厦门": "350200",
    "昆明": "530100", "济南": "370100",
    "福州": "350100", "合肥": "340100",
    "南昌": "360100", "贵阳": "520100",
    "南宁": "450100", "海口": "460100",
    "兰州": "620100", "太原": "140100",
    "石家庄": "130100", "哈尔滨": "230100",
    "长春": "220100", "呼和浩特": "150100",
    "银川": "640100", "西宁": "630100",
    "乌鲁木齐": "650100", "拉萨": "540100",
    "宁波": "330200", "无锡": "320200",
    "佛山": "440600", "东莞": "441900",
    # ===== Triply 知识库已覆盖、但原表未收录的国内目的地（补全以稳定天气解析，避免回落限流的 geo 接口）=====
    "延吉": "222401", "延吉市": "222401",
    "三亚": "460200", "三亚市": "460200",
    "丽江": "530700", "丽江市": "530700",
    "桂林": "450300", "桂林市": "450300",
    "张家界": "430800", "张家界市": "430800",
    "大理": "532900", "大理市": "532901",
    "九寨沟": "513225", "九寨沟县": "513225",
    "峨眉山": "511181", "峨眉山市": "511181",
    "承德": "130800", "承德市": "130800",
    "北戴河": "130304", "北戴河区": "130304",
    "秦皇岛": "130300", "秦皇岛市": "130300",
    "洛阳": "410300", "洛阳市": "410300",
    "开封": "410200", "开封市": "410200",
    "平遥": "140728", "平遥县": "140728",
    "黄山": "341000", "黄山市": "341000",
    "婺源": "361130", "婺源县": "361130",
    "武夷山": "350782", "武夷山市": "350782",
    "敦煌": "620982", "敦煌市": "620982",
    "喀什": "653100", "喀什市": "653101",
    "荔波": "522722", "荔波县": "522722",
    "北海": "450500", "北海市": "450500",
    }

# 海外城市：高德天气仅覆盖中国境内，这些城市无法解析 adcode，直接给友好提示。
INTERNATIONAL_CITIES = {
    "东京": "日本", "东京都": "日本", "大阪": "日本", "京都": "日本", "北海道": "日本",
    "首尔": "韩国", "釜山": "韩国", "济州岛": "韩国",
    "曼谷": "泰国", "清迈": "泰国", "普吉": "泰国", "普吉岛": "泰国",
    "新加坡": "新加坡",
    "巴黎": "法国", "尼斯": "法国",
    "伦敦": "英国", "纽约": "美国", "洛杉矶": "美国", "旧金山": "美国",
    "罗马": "意大利", "悉尼": "澳大利亚", "墨尔本": "澳大利亚",
    "迪拜": "阿联酋", "巴厘岛": "印度尼西亚",
}


# 国家前缀集合（用于规范化："日本东京" -> "东京"，避免 LLM 带国家名导致查不到）
COUNTRY_PREFIXES = set(INTERNATIONAL_CITIES.values())


def _normalize_location(location: str) -> str:
    """去掉开头的国家名与结尾的'市/区/县'，得到纯城市名，
    兼容 LLM 传入'日本东京''泰国曼谷'这类带国家前缀的写法。"""
    loc = (location or "").strip()
    for country in COUNTRY_PREFIXES:
        # 仅当后面还有城市名时才剥离（避免把"新加坡"本身剥空）
        if loc.startswith(country) and len(loc) > len(country):
            loc = loc[len(country):].strip()
            break
    if loc.endswith(("市", "区", "县")):
        loc = loc[:-1]
    return loc


async def _resolve_adcode(location: str) -> str:
    """
    将城市名/地名解析为高德 adcode；若入参已是 6 位 adcode 则直接返回。
    优先查本地速查表，仅在表外城市/区县时才回退到高德 geo 接口。
    """
    location = (location or "").strip()
    if location.isdigit() and len(location) == 6:
        return location
    norm = _normalize_location(location)
    # 1) 本地速查表（稳定、不耗配额、不限流）
    if location in CITY_ADCODE_MAP:
        return CITY_ADCODE_MAP[location]
    if norm and norm in CITY_ADCODE_MAP:
        return CITY_ADCODE_MAP[norm]
    # 兼容带"市"后缀的写法（如"西安市" -> "西安"）
    # 2) 海外城市：高德天气仅覆盖中国，直接返回友好提示标记，避免无效 geo 调用
    #    兼容"日本东京""泰国曼谷"等带国家前缀的写法
    for key in (location, norm):
        if key and key in INTERNATIONAL_CITIES:
            return f"__INTL__:{INTERNATIONAL_CITIES[key]}"
    # 3) 兜底：调用高德 geo 接口（处理表外城市/区县）
    if not AMAP_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                AMAP_GEO_URL,
                params={"key": AMAP_API_KEY, "address": location, "output": "JSON"},
            )
            geo = resp.json()
            if geo.get("status") != "1" or not geo.get("geocodes"):
                return ""
            return geo["geocodes"][0].get("adcode", "")
    except Exception:
        return ""


@mcp.tool()
async def get_weather_forecast(city_adcode: str) -> str:
    """
    查询城市未来天气预报

    Args:
        city_adcode: 城市/区域的 adcode 编码 (例如: 北京="110000", 上海="310000",
                     西安="610100", 成都="510100", 深圳="440300", 杭州="330100",
                     广州="440100", 南京="320100", 重庆="500000", 武汉="420100")。
                     注意：API 不支持直接使用中文城市名，必须使用 adcode。

    Returns:
        JSON 格式的未来天气预报数据 (包含白天/晚上的天气、温度、风力等)。
    """

    if not AMAP_API_KEY:
        return json.dumps({"error": "未配置 AMAP_API_KEY"}, ensure_ascii=False)

    # 兼容城市名与 adcode：先解析为 adcode 再查天气
    adcode = await _resolve_adcode(city_adcode)
    if not adcode:
        return json.dumps({"error": f"无法解析城市: {city_adcode}"}, ensure_ascii=False)
    # 海外城市：高德不覆盖，返回友好提示而非报错
    if adcode.startswith("__INTL__:"):
        country = adcode.split(":", 1)[1]
        return json.dumps({
            "info": f"海外城市（{country}）暂不支持实时天气查询，请自行查看目的地当地天气",
            "supported": False,
        }, ensure_ascii=False)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # async with .... 开启异步池
        try:
            # client.get：向 API 发送指令
            response = await client.get(
                AMAP_WEATHER_URL,
                params={
                    "key": AMAP_API_KEY,
                    "city": adcode,
                    "extensions": "all",  # all 代表我们要查询“预报天气”
                    "output": "JSON"
                }
            )

            data = response.json()  # 将返回的字符串转成 Python 字典

            if data.get("status") != "1":  # 高德 API 约定 status="1" 才是成功
                return json.dumps({
                    "error": data.get("info", "查询失败"),
                    "infocode": data.get("infocode")
                }, ensure_ascii=False)

            # ... 后面是提取具体的 city, province, casts (天气列表)
            forecasts = data.get("forecasts", [])
            if not forecasts:
                return json.dumps({"error": "未找到天气数据"}, ensure_ascii=False)

            forecast = forecasts[0]  # 提取第一个城市的天气对象

            result = {
                "city": forecast.get("city"),
                "adcode": forecast.get("adcode"),
                "province": forecast.get("province"),
                "reporttime": forecast.get("reporttime"),
                "casts": forecast.get("casts", [])  # 从第一个城市结果里拿未来几天的天气数组
            }

            # indent=2代表每一层缩进 2 个空格
            return json.dumps(result, ensure_ascii=False, indent=2)

        except httpx.TimeoutException:
            return json.dumps({"error": "请求超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")