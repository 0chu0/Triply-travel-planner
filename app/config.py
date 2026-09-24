"""
配置管理模块
使用 pydantic-settings 管理环境变量
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache

# 获取当前文件的上级目录（即项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Settings(BaseSettings):
    """应用配置"""

    # ============== 应用基础配置 ==============
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    # ============== 注册与种子账号 ==============
    allow_open_registration: bool = Field(default=True, alias="ALLOW_OPEN_REGISTRATION")
    bootstrap_admin_username: str = Field(default="", alias="BOOTSTRAP_ADMIN_USERNAME")
    bootstrap_admin_password: str = Field(default="", alias="BOOTSTRAP_ADMIN_PASSWORD")

    # ============== Token 配额（应用层限流，防止被白嫖） ==============
    # 每个账号默认可用 token 上限；用量记在 token_usage 表，超出后暂停对话
    default_user_token_quota: int = Field(default=200_000, alias="DEFAULT_USER_TOKEN_QUOTA")
    # 额度用尽时页面提示的联系方式（邮箱/微信），留空则只提示「请联系管理员」
    quota_request_contact: str = Field(default="", alias="QUOTA_REQUEST_CONTACT")

    # ============== LLM 配置 ==============
    dashscope_api_key: str = Field(alias="DASHSCOPE_API_KEY")
    qwen_model_name: str = Field(default="qwen3.7-flash", alias="QWEN_MODEL_NAME")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL"
    )
    qwen_temperature: float = 0.7
    # 单轮最大输出 token 数。
    # 关键成本阀门：8000 → 2500 让"长输出"成为不可能，
    # 配合 GLOBAL_OUTPUT_RULES 的"单次回复 ≤ 8 行"硬约束，从源头堵住单轮 token 爆炸。
    # 如果业务需要更长回答，按 step 单点覆盖即可（不要直接调大这个默认值）。
    qwen_max_tokens: int = 2500

    # ============== Langfuse 追踪配置（替代 LangSmith） ==============
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        alias="LANGFUSE_HOST",
    )
    langfuse_tracing_enabled: bool = Field(default=True, alias="LANGFUSE_TRACING_ENABLED")
    langfuse_project: str = Field(default="zhixing-travel-planer-dev", alias="LANGFUSE_PROJECT")

    @property
    def langfuse_enabled(self) -> bool:
        """仅在显式开启追踪且公私钥均配置（非占位值）时才启用 Langfuse。"""
        if not self.langfuse_tracing_enabled:
            return False
        placeholders = ("REPLACE_ME", "请替换", "your-")
        pk = self.langfuse_public_key
        sk = self.langfuse_secret_key
        if not pk or not sk:
            return False
        if any(p in pk for p in placeholders) or any(p in sk for p in placeholders):
            return False
        return True

    # ============== 数据库配置 ==============
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")

    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")

    # ============== MCP 服务配置 ==============
    amap_api_key: str = Field(default="", alias="AMAP_API_KEY")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),     # 自动拼接路径，不管代码在哪运行都能找到
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"              # 忽略 .env 中多余的字段，防止报错
    )

    @property
    def database_url(self) -> str:
        """生成 PostgreSQL 连接字符串"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """生成 Redis 连接字符串"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """获取配置单例（缓存）"""
    return Settings()


# 全局配置对象
settings = get_settings()

# 测试
# if __name__ == '__main__':
#     print(settings.database_url)
#     print(settings.redis_url)
#     print(settings.qwen_model_name)
#     print(settings.qwen_base_url)
#     print(settings.qwen_temperature)
#     print(settings.qwen_max_tokens)
#     print(settings.langfuse_secret_key)