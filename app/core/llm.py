"""
LLM 工厂 —— 按「环节难度」分配模型档位（tier），而不是全局一个模型

为什么要分档（2026-10-09 实测 + 官方价核对）
--------------------------------------------
百炼官方价（元 / 百万 tokens）：

    qwen3.8-max     输入 12    缓存命中 1.5    输出 36
    qwen3.8-flash   输入 0.8   缓存命中 0.1    输出 2.7
    qwen3.7-flash   输入 0.2   缓存命中 0.04   输出 0.8

旗舰档与轻量档相差约 15~60 倍。而本项目的 token 结构实测是 **输入占 96%**
（quick 档 8 条：平均输入 13,749 / 输出 560），也就是说"每轮重放一遍上下文"
才是主要成本，那么"哪些环节配得上旗舰价"就成了一个能直接省钱、
又几乎不损失质量的开关。

分档原则
--------
- main（权衡题）：跨约束推理、编排、计算 —— 行程编排、预算、
  目的地/交通/住宿的取舍。错了要返工、返工又是一整轮输入。
- light（选择题）：查询改写、重排、意图分类、结构化参数抽取、
  格式化输出、简单问答。输入输出都短、答案有明确对错。

安全阀
------
把 .env 里的 QWEN_LIGHT_MODEL_NAME 设成与 QWEN_MODEL_NAME 同一个模型
（例如都写 qwen3.8-max），就等价于"全量回退到旗舰档"，不需要改任何代码。
"""
from functools import lru_cache
from typing import Optional

from langchain_openai import ChatOpenAI

from app.config import settings

MAIN = "main"
LIGHT = "light"


def build_chat_model(
    tier: str = MAIN,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: bool = True,
) -> ChatOpenAI:
    """构造一个 ChatOpenAI 实例。

    Args:
        tier: "main" → settings.qwen_model_name；"light" → settings.qwen_light_model_name
        temperature: 覆盖默认温度（不传则用 settings.qwen_temperature）
        max_tokens: 覆盖单轮输出上限（不传则用 settings.qwen_max_tokens）
        streaming: 是否流式；子 Agent / 结构化抽取类调用可关掉
    """
    if tier not in (MAIN, LIGHT):
        raise ValueError(f"未知模型档位: {tier!r}（只能是 'main' 或 'light'）")

    model_name = (
        settings.qwen_model_name if tier == MAIN else settings.qwen_light_model_name
    )

    return ChatOpenAI(
        model=model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.qwen_temperature if temperature is None else temperature,
        max_tokens=settings.qwen_max_tokens if max_tokens is None else max_tokens,
        streaming=streaming,
        # 关思考：推理 token 按输出价计费，xhigh 每轮额外烧 2k~4k，关掉后思维链归零。
        # 开上下文缓存：固定前缀（系统提示 / 输出规范 / 工具 schema）命中后按缓存价计费。
        # 注意它只是"允许命中"，命中率取决于前缀是否稳定（见 README 降本章节）。
        extra_body={"enable_thinking": False, "enable_context_cache": True},
    )


@lru_cache(maxsize=None)
def get_main_llm() -> ChatOpenAI:
    """主档位模型（进程内单例，主对话链路按步骤切换用）。"""
    return build_chat_model(MAIN)


@lru_cache(maxsize=None)
def get_light_llm() -> ChatOpenAI:
    """轻量档位模型（进程内单例，主对话链路按步骤切换用）。"""
    return build_chat_model(LIGHT)
