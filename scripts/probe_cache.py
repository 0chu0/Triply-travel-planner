"""
上下文缓存命中率探针（一次性诊断脚本，不进入生产链路）

回答两个问题：
1. 百炼侧「上下文缓存」到底有没有命中？命中多少 token？
2. 流式（生产实际用的调用方式）下能不能拿到 usage？拿不到会怎样？

结论会直接影响「降本」该往哪个方向使劲：
- 若 cache_read ≈ 0，说明 enable_context_cache 白开，成本就是原价，
  那么降本只能靠「缩短输入」和「换便宜模型」，不能指望缓存折扣。
- 若流式拿不到 usage，则 Langfuse 的 Usage/Cost 列永远为空，
  且 chat.py 的配额结算会走 estimate_tokens 估算兜底（扣得不准）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm import build_chat_model  # noqa: E402

# 模拟真实链路：一段很长的固定前缀（系统提示 + 工具 schema 的替身）+ 变化的尾巴
# ⚠️ 前缀长度是关键变量：各家上下文缓存都有「最小可缓存粒度」（通义/百炼为 1024 tokens）。
# 前缀太短会永远 cache_read=0，得出「缓存没生效」的错误结论。因此这里做两档：
#   短前缀 < 1024  → 预期不命中（验证粒度门槛存在）
#   长前缀 > 1024  → 若仍不命中，才能说明缓存确实没生效
FIXED_PREFIX = (
    "你是 Triply 旅行规划助手，负责为用户规划行程。"
    "以下是全局输出规范，必须严格遵守："
    "【格式】不要使用 Markdown 标记，用【】代替标题；正文不超过 8 行；"
    "不要暴露内部工具名；不要否认已接入的能力。"
    "以下是可用工具的说明：" + ("工具条目：查询天气、查询航班、查询酒店、检索攻略。" * 60)
)
LONG_PREFIX = FIXED_PREFIX + ("补充工具条目：高铁查询、自驾路线、景点门票、餐饮推荐、预算汇总。" * 120)
TAIL_A = "\n\n用户问题：北京明天天气怎么样？"
TAIL_B = "\n\n用户问题：上海后天天气怎么样？"


def show(tag: str, usage):
    if not usage:
        print(f"  [{tag}] ❌ 没有 usage（流式下若如此，配额将走估算兜底）")
        return
    det = usage.get("input_token_details") or {}
    inp = usage.get("input_tokens", 0)
    cached = det.get("cache_read") or det.get("cache_creation") or 0
    ratio = (cached / inp * 100) if inp else 0.0
    print(
        f"  [{tag}] input={inp} output={usage.get('output_tokens', 0)} "
        f"cache_read={det.get('cache_read')} → 命中率 {ratio:.1f}%"
    )
    print(f"        input_token_details 全字段 = {det}")


async def main():
    llm = build_chat_model("main")
    print(f"模型 = {llm.model_name}")
    print(f"enable_context_cache = {(llm.extra_body or {}).get('enable_context_cache')}")
    print(f"streaming = {llm.streaming}   stream_usage = {getattr(llm, 'stream_usage', '(无此属性)')}")
    print()

    print("=== 1) 短前缀（<1024 tokens）：预期不命中，用于确认粒度门槛存在 ===")
    for i, tail in enumerate([TAIL_A, TAIL_B], 1):
        r = await llm.ainvoke([{"role": "user", "content": FIXED_PREFIX + tail}])
        show(f"短前缀第{i}次", r.usage_metadata)

    print()
    print("=== 2) 长前缀（>1024 tokens）：连续 3 次，第 2 次起才是缓存生效的判据 ===")
    for i, tail in enumerate([TAIL_A, TAIL_B, TAIL_A], 1):
        r = await llm.ainvoke([{"role": "user", "content": LONG_PREFIX + tail}])
        show(f"长前缀第{i}次", r.usage_metadata)

    print()
    print("=== 3) 流式：生产实际用的调用方式（长前缀，连调 2 次）===")
    for i, tail in enumerate([TAIL_A, TAIL_B], 1):
        acc = None
        async for chunk in llm.astream([{"role": "user", "content": LONG_PREFIX + tail}]):
            acc = chunk if acc is None else acc + chunk
        show(f"流式第{i}次", acc.usage_metadata if acc else None)


if __name__ == "__main__":
    asyncio.run(main())
