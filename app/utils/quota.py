"""
Token 配额工具：读写账号用量、判断是否超限、估算兜底

要点
----
- 真正计费的是「持有 DASHSCOPE_API_KEY 的那个账号」，本模块只在应用层做
  「按账号限额」，用于防止公网被白嫖，不改变云厂商账单口径。
- 优先使用大模型返回的 usage_metadata（精确）；个别模型/网关不回 usage 时，
  用 estimate_tokens() 兜底估算，保证不会出现「用量永远为 0」的漏计。
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.usage import TokenUsage
from app.utils.logger import app_logger


def estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数（兜底用）。

    Qwen 系列对中文大致 1 token ≈ 1.5 个汉字，英文约 4 字符/token，
    这里取 1.6 的中庸系数；仅用于模型未返回 usage 时的兜底，允许有偏差。
    """
    if not text:
        return 0
    return max(1, int(len(text) / 1.6))


def effective_quota(usage: Optional[TokenUsage]) -> int:
    """该账号的实际配额：账号级配额优先，为 0 则回落到全局默认"""
    if usage is not None and usage.quota_tokens and usage.quota_tokens > 0:
        return int(usage.quota_tokens)
    return int(settings.default_user_token_quota)


def build_usage_payload(usage: Optional[TokenUsage]) -> dict:
    """组装给前端展示的用量信息"""
    quota = effective_quota(usage)
    used = int(usage.used_tokens or 0) if usage else 0
    remaining = max(0, quota - used)
    percent = round(used / quota * 100, 1) if quota > 0 else 0.0

    return {
        "used_tokens": used,
        "quota_tokens": quota,
        "remaining_tokens": remaining,
        "percent": percent,
        "request_count": int(usage.request_count or 0) if usage else 0,
        "exceeded": quota > 0 and used >= quota,
        # 便于前端在页面上展示「如何申请」
        "contact": settings.quota_request_contact,
    }


async def get_or_create_usage(db: AsyncSession, user_id) -> TokenUsage:
    """
    取用户的用量记录，不存在则创建（不 commit，由调用方决定事务边界）。
    """
    result = await db.execute(select(TokenUsage).where(TokenUsage.user_id == user_id))
    usage = result.scalar_one_or_none()

    if usage is None:
        usage = TokenUsage(user_id=user_id, quota_tokens=0)
        db.add(usage)
        await db.flush()

    return usage


async def record_usage(
        db: AsyncSession,
        user_id,
        prompt_tokens: int,
        completion_tokens: int,
) -> Optional[TokenUsage]:
    """
    原子累加一次对话的 token 消耗，返回最新用量。

    用 SQL 侧的 `col = col + n` 更新（而不是先读后写），避免并发请求互相覆盖。
    任何异常都不应影响主对话流程，因此这里吞掉异常只记日志。
    """
    p = max(0, int(prompt_tokens))
    c = max(0, int(completion_tokens))

    try:
        # 保证用量行存在（首次对话时创建）
        await get_or_create_usage(db, user_id)
        await db.commit()

        await db.execute(
            update(TokenUsage)
            .where(TokenUsage.user_id == user_id)
            .values(
                used_tokens=TokenUsage.used_tokens + p + c,
                prompt_tokens=TokenUsage.prompt_tokens + p,
                completion_tokens=TokenUsage.completion_tokens + c,
                request_count=TokenUsage.request_count + 1,
                updated_at=func.now(),
            )
        )
        await db.commit()

        result = await db.execute(select(TokenUsage).where(TokenUsage.user_id == user_id))
        return result.scalar_one_or_none()
    except Exception as e:
        app_logger.warning(f"⚠️ 记录 token 用量失败（已忽略）: {e}")
        await db.rollback()
        return None
