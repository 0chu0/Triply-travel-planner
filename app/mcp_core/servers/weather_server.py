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
}


async def _resolve_adcode(location: str) -> str:
    """
    将城市名/地名解析为高德 adcode；若入参已是 6 位 adcode 则直接返回。
    优先查本地速查表，仅在表外城市/区县时才回退到高德 geo 接口。
    """
    location = (location or "").strip()
    if location.isdigit() and len(location) == 6:
        return location
    # 1) 本地速查表（稳定、不耗配额、不限流）
    if location in CITY_ADCODE_MAP:
        return CITY_ADCODE_MAP[location]
    # 兼容带"市"后缀的写法（如"西安市" -> "西安"）
    if location.endswith("市") and location[:-1] in CITY_ADCODE_MAP:
        return CITY_ADCODE_MAP[location[:-1]]
    # 2) 兜底：调用高德 geo 接口（处理表外城市/区县）
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