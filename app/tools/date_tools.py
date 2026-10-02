"""
本地确定性工具：获取当前真实日期

原先「今天几号」依赖 VariFlight 航班服务的 getTodayDate MCP 工具——
航班服务一旦挂掉或 key 失效，日期能力就跟着丢失（单点依赖）。
「当前日期」是确定性逻辑，本机 datetime 即可算出，改为零依赖的本地工具。
"""

from langchain_core.tools import tool
from datetime import datetime

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@tool
def get_today_date() -> str:
    """返回当前真实日期（本地时区），格式 YYYY-MM-DD 及星期几，例如 2026-10-02（周五）。

    当用户使用相对或模糊时间（今天/明天/后天/下周/下个月/春节/五一/暑假/这周几/下周几等）时，
    必须先调用本工具获取真实日期与星期，再把相对时间换算为具体日期或日期范围，并向用户确认。
    不要凭记忆猜测“今天”是几号。"""
    now = datetime.now()
    weekday = _WEEKDAYS[now.weekday()]
    return f"{now.strftime('%Y-%m-%d')}（{weekday}）"
