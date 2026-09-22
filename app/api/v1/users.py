"""
用户管理 API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.base import get_db, async_session_maker
from app.models.user import User
from app.models.usage import QuotaRequest
from app.schemas.user import (
    UserRegister, UserLogin, UserResponse, TokenResponse,
    UsageResponse, QuotaRequestCreate,
)
from app.utils.security import hash_password, verify_password, create_access_token
from app.utils.quota import get_or_create_usage, build_usage_payload, effective_quota
from app.api.dependencies import get_current_user
from app.config import settings
from app.utils.logger import app_logger

router = APIRouter(prefix="/users", tags=["用户管理"])


@router.post("/register", response_model=TokenResponse)
async def register(
        user_data: UserRegister,
        db: AsyncSession = Depends(get_db)
):
    """用户注册"""

    # 关闭开放注册：仅允许已有账号登录，避免公网被白嫖 token
    if not settings.allow_open_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="注册已关闭，请联系管理员获取账号"
        )

    # 检查用户名是否存在
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )

    # 检查邮箱是否存在
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册"
        )

    # 创建用户
    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        preferences={}
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    # 生成 JWT
    access_token = create_access_token(data={"sub": str(user.id)})

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.from_orm(user)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
        credentials: UserLogin,
        db: AsyncSession = Depends(get_db)
):
    """用户登录"""

    # 查询用户
    result = await db.execute(select(User).where(User.username == credentials.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    # 生成 JWT
    access_token = create_access_token(data={"sub": str(user.id)})

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.from_orm(user)
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
        user: User = Depends(get_current_user)
):
    """获取当前用户信息"""
    return UserResponse.from_orm(user)


@router.get("/usage", response_model=UsageResponse)
async def get_my_usage(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """获取当前账号的 token 用量与配额（前端用于展示进度条/余量）"""
    usage = await get_or_create_usage(db, user.id)
    await db.commit()
    return UsageResponse(**build_usage_payload(usage))


@router.post("/quota-request")
async def request_more_quota(
        payload: QuotaRequestCreate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    额度用尽后自助提交提额申请。

    同一账号只保留一条 pending 申请，重复提交返回同一条，避免刷屏。
    """
    usage = await get_or_create_usage(db, user.id)
    quota = effective_quota(usage)
    used = int(usage.used_tokens or 0)

    if quota > 0 and used < quota:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"当前额度尚未用尽（{used}/{quota}），无需申请"
        )

    result = await db.execute(
        select(QuotaRequest)
        .where(QuotaRequest.user_id == user.id)
        .where(QuotaRequest.status == "pending")
        .order_by(QuotaRequest.created_at.desc())
    )
    existing = result.scalars().first()

    if existing:
        return {
            "status": "pending",
            "message": "你的提额申请已在处理中，请耐心等待～",
            "request_id": str(existing.id),
            "contact": settings.quota_request_contact,
        }

    req = QuotaRequest(
        user_id=user.id,
        username=user.username,
        email=user.email,
        reason=payload.reason or "",
        status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    app_logger.info(f"📩 收到提额申请: {user.username}（已用 {used}/{quota}）")

    return {
        "status": "pending",
        "message": "申请已提交，管理员会尽快为你开通更多额度～",
        "request_id": str(req.id),
        "contact": settings.quota_request_contact,
    }


def _require_admin(user: User):
    """简易管理员校验：用户名等于 .env 中配置的 BOOTSTRAP_ADMIN_USERNAME"""
    if not settings.bootstrap_admin_username or user.username != settings.bootstrap_admin_username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可操作"
        )


@router.get("/quota-requests")
async def list_quota_requests(
        limit: int = 50,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """查看提额申请列表（仅管理员）"""
    _require_admin(user)

    result = await db.execute(
        select(QuotaRequest)
        .order_by(QuotaRequest.created_at.desc())
        .limit(limit)
    )
    return {"items": [r.to_dict() for r in result.scalars().all()]}


@router.post("/quota/grant")
async def grant_quota(
        username: str,
        quota_tokens: int,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    给指定账号调整额度（仅管理员）。quota_tokens 为新的总配额，传 0 表示跟随全局默认。
    """
    _require_admin(user)

    result = await db.execute(select(User).where(User.username == username))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    usage = await get_or_create_usage(db, target.id)
    usage.quota_tokens = max(0, quota_tokens)
    await db.commit()
    await db.refresh(usage)

    # 提额后把该用户的 pending 申请标记为已处理
    if usage.quota_tokens == 0 or usage.used_tokens < usage.quota_tokens:
        result = await db.execute(
            select(QuotaRequest)
            .where(QuotaRequest.user_id == target.id)
            .where(QuotaRequest.status == "pending")
        )
        for req in result.scalars().all():
            req.status = "approved"
        await db.commit()

    app_logger.info(f"🔧 管理员调整额度: {username} → {quota_tokens}")
    return build_usage_payload(usage)


async def bootstrap_admin():
    """
    启动时创建初始管理员账号。

    关闭开放注册（ALLOW_OPEN_REGISTRATION=false）后，公网无法自助注册，
    这里保证所有者始终有一个可登录的账号。仅当账号不存在时创建，重复启动安全。
    """
    try:
        username = settings.bootstrap_admin_username
        password = settings.bootstrap_admin_password
        if not username or not password:
            app_logger.info("未配置 BOOTSTRAP_ADMIN 账号，跳过初始账号创建")
            return

        async with async_session_maker() as session:
            result = await session.execute(select(User).where(User.username == username))
            if result.scalar_one_or_none():
                app_logger.info(f"初始管理员账号已存在，跳过: {username}")
                return

            user = User(
                username=username,
                email=f"{username}@bootstrap.local",
                password_hash=hash_password(password),
                preferences={},
            )
            session.add(user)
            await session.commit()
            app_logger.info(f"✅ 初始管理员账号已创建: {username} / {password}")
    except Exception as e:
        app_logger.warning(f"⚠️ 初始管理员账号创建失败（已忽略，不影响启动）: {e}")