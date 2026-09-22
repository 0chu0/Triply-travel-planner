"""
测试千问 LLM 连接
使用 OpenAI 兼容端点（与 app/config.py 一致），支持新版模型如 qwen3.7-flash
"""
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，使 `import app` 在 `uv run python scripts/test_llm.py` 这种方式下也可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from app.core.tracing import get_langfuse_handler, flush_langfuse

load_dotenv()


def test_qwen_connection():
    """测试千问模型连接"""

    print("测试千问模型连接...")

    try:
        # 读取 .env 配置（与 app/config.py 保持一致）
        api_key = os.getenv("DASHSCOPE_API_KEY")
        base_url = os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model_name = os.getenv("QWEN_MODEL_NAME", "qwen3.7-flash")

        # 初始化模型：OpenAI 兼容端点，支持新版 qwen 模型，temperature 也能生效
        model = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0.7,
        )

        # 注入 Langfuse 链路追踪（未配置时返回 None，不影响主流程）
        handler = get_langfuse_handler()
        callbacks = [handler] if handler else {}

        try:
            # 发送测试消息
            response = model.invoke(
                [HumanMessage(content="你好，请用一句话介绍你自己。")],
                config={"callbacks": callbacks} if callbacks else {},
            )
        finally:
            # 确保异步 trace 在本次进程退出前上报完成
            # 注意：CallbackHandler 没有 flush()，需刷新底层 Langfuse client
            if handler is not None:
                try:
                    flush_langfuse()
                except Exception:
                    pass

        print(f"✅ 连接成功！模型回复：\n{response.content}")
        if handler:
            print("📊 Langfuse 追踪已启用，可在 Langfuse 控制台查看本次 trace。")
        else:
            print("ℹ️  Langfuse 未启用（未配置公私钥或追踪关闭），本次仅验证 LLM 连接。")

    except Exception as e:
        print(f"❌ 连接失败：{e}")
        raise


if __name__ == "__main__":
    test_qwen_connection()
