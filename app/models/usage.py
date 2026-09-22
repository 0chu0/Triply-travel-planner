"""
Token 用量与配额模型

设计说明
--------
1. 不修改已有 user 表：项目用 `Base.metadata.create_all` 建表，它只会创建
   「不存在的表」，不会给已存在的表加列。所以把用量/配额独立成一张一对一表，
   部署时直接跑 init_db.py 即可自动建好，零迁移风险。
2. quota_tokens = 0 表示「跟随全局默认配额」（settings.default_user_token_quota）。
   想给某个人单独加量时，只改这一行的 quota_tokens 即可，无需动配置。
"""
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class TokenUsage(Base):
    """账号 token 用量（每个用户一条）"""

    __tablename__ = "token_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # 累计消耗
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    # 0 表示使用全局默认配额
    quota_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    request_count: Mapped[int] = mapped_column(BigInteger, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now()
    )


class QuotaRequest(Base):
    """额度提升申请（额度用尽后，用户可在页面上自助提交）"""

    __tablename__ = "quota_request"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True
    )

    username: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(Text, default="")

    # pending / approved / rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        index=True
    )
