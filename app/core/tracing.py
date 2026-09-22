"""
Langfuse 链路追踪（替代 LangSmith）

提供 get_langfuse_handler()，返回 LangChain/LangGraph 的 CallbackHandler。
将返回的 handler 注入到 model / agent 的调用中即可保留链路追踪能力：

    from app.core.tracing import get_langfuse_handler, flush_langfuse

    handler = get_langfuse_handler()
    result = agent.invoke(input, config={"callbacks": [handler]} if handler else {})
    flush_langfuse()   # 短进程/脚本中务必调用，确保 trace 上报完成

兼容要点（langfuse 4.x）：
- 若未配置公私钥（占位值）或追踪关闭，get_langfuse_handler() 返回 None，主流程不受影响。
- 密钥与开关统一从 app.config.settings 读取（即 .env 的 LANGFUSE_* 变量）。
- ⚠️ 关键：CallbackHandler 内部通过 get_client(public_key=...) 在「已注册实例表」里查找客户端。
  若进程中从未用该 public_key 构造过 Langfuse 实例，查找会失败，SDK 会退化成
  tracing_enabled=False 的假客户端（public_key="fake"），trace 被静默丢弃。
  因此这里必须先显式构造 Langfuse(...) 完成注册，再创建 CallbackHandler。
- ⚠️ CallbackHandler 本身没有 flush() 方法，需调用底层 client 的 flush()（见 flush_langfuse）。
"""
from functools import lru_cache

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.config import settings


@lru_cache
def _get_client() -> "Langfuse | None":
    """按配置初始化并注册 Langfuse 客户端；未启用时返回 None。"""
    if not settings.langfuse_enabled:
        return None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


@lru_cache
def get_langfuse_handler() -> "CallbackHandler | None":
    """构造 Langfuse CallbackHandler；未启用/未配置时返回 None。"""
    # 先确保客户端已注册（否则 handler 会拿到 fake 客户端，trace 丢失）
    client = _get_client()
    if client is None:
        return None
    return CallbackHandler(public_key=settings.langfuse_public_key)


def flush_langfuse() -> None:
    """刷新 Langfuse 上报队列，确保进程退出前 trace 已发送。

    短脚本 / agent 单次运行后建议调用一次；常驻服务可不调。
    """
    client = _get_client()
    if client is not None:
        client.flush()
