"""
用户管理 API
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.base import get_db, async_session_maker
from app.models.user import User
from app.models.usage import QuotaRequest, TokenUsage
from app.schemas.user import (
    UserRegister, UserLogin, UserResponse, TokenResponse,
    UsageResponse, QuotaRequestCreate,
    AdminQuotaDashboardRow, AdminQuotaDashboardResponse,
)
from app.utils.security import hash_password, verify_password, create_access_token
from app.utils.quota import get_or_create_usage, build_usage_payload, effective_quota
from app.api.dependencies import get_current_user
from app.config import settings
from app.utils.logger import app_logger

router = APIRouter(prefix="/users", tags=["用户管理"])


def _is_admin(user: User) -> bool:
    """管理员判定：用户名等于 .env 中配置的 BOOTSTRAP_ADMIN_USERNAME"""
    return bool(settings.bootstrap_admin_username) and user.username == settings.bootstrap_admin_username


def _user_payload(user: User) -> UserResponse:
    """
    统一构造用户响应。

    额外带上 is_admin，前端据此决定是否显示「消息通知」入口。
    注意：这只是给前端做 UI 显隐，接口鉴权仍由 _require_admin 负责。
    """
    payload = UserResponse.model_validate(user)
    payload.is_admin = _is_admin(user)
    return payload


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
        user=_user_payload(user)
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
        user=_user_payload(user)
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
        user: User = Depends(get_current_user)
):
    """获取当前用户信息"""
    return _user_payload(user)


@router.get("/usage", response_model=UsageResponse)
async def get_my_usage(
        response: Response,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    获取当前账号的 token 用量与配额（前端用于展示进度条/余量）

    该接口会被前端频繁刷新（额度看板、弹窗「刷新额度」按钮），
    必须显式禁止一切缓存（浏览器启发式缓存 / 中间代理），
    否则管理员点「通过」后，游客端可能一直拿到旧的 GET 缓存，
    出现「审批通过了但前端额度不更新」的假死现象。
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"

    usage = await get_or_create_usage(db, user.id)
    await db.commit()

    # 是否已有待处理的提额申请：决定前端「申请更多额度」按钮的可用状态。
    # 单独查一次（简单可读），数据量很小（每用户最多几条），无需聚合查询。
    pending_row = await db.execute(
        select(QuotaRequest.id)
        .where(QuotaRequest.user_id == user.id)
        .where(QuotaRequest.status == "pending")
        .limit(1)
    )
    has_pending = pending_row.first() is not None

    return UsageResponse(**build_usage_payload(usage, has_pending_request=has_pending))


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
    if not _is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可操作"
        )


async def _get_quota_request(db: AsyncSession, request_id: str) -> QuotaRequest:
    """按 request_id 取申请记录；ID 非法或不存在时抛 4xx"""
    try:
        rid = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="申请 ID 格式不正确"
        )

    result = await db.execute(select(QuotaRequest).where(QuotaRequest.id == rid))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="申请记录不存在"
        )
    return req


def _request_payload(r: QuotaRequest) -> dict:
    """
    申请记录的对外表示。

    出于保密考虑只回传「电子邮箱」，不回传用户名 / user_id：
    管理员据此联系申请人；要给人加量时改用 request_id（见 approve 接口），
    因此前端不需要、也拿不到用户名。
    """
    return {
        "request_id": str(r.id),
        "email": r.email or "",
        "reason": r.reason or "",
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


@router.get("/quota-requests")
async def list_quota_requests(
        limit: int = 50,
        status_filter: str = "",
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    查看提额申请列表（仅管理员）。

    status_filter 可传 pending / approved / rejected，留空表示全部。
    返回项只含邮箱（不含用户名），并附带全局待处理数量，供前端做角标。
    """
    _require_admin(user)

    stmt = select(QuotaRequest)
    if status_filter:
        stmt = stmt.where(QuotaRequest.status == status_filter)
    stmt = stmt.order_by(QuotaRequest.created_at.desc()).limit(max(1, min(limit, 200)))

    result = await db.execute(stmt)
    items = [_request_payload(r) for r in result.scalars().all()]

    pending_result = await db.execute(
        select(func.count())
        .select_from(QuotaRequest)
        .where(QuotaRequest.status == "pending")
    )
    pending_count = int(pending_result.scalar() or 0)

    return {"items": items, "pending_count": pending_count}


@router.post("/quota-requests/{request_id}/approve")
async def approve_quota_request(
        request_id: str,
        quota_tokens: int = 20_000,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    批准一条提额申请（仅管理员）。

    按 request_id 定位申请人（前端只知道邮箱，不知道用户名），
    并把该申请标记为 approved。

    【关键语义】quota_tokens 是「追加量」而非「总配额」：
        新配额 = 当前已用量 + quota_tokens
    为什么不能直接设成绝对值——如果游客已用量已经超过授予量
    （例如已用 5.5 万、授予 2 万），绝对值写法会让 used >= quota
    依旧成立，批准等于没批，游客端立刻还是 402。
    追加式写法则保证游客一定获得 quota_tokens 个全新可用 token，
    且 used_tokens 的累计账目不被清零，额度看板数据保持真实。
    """
    _require_admin(user)

    req = await _get_quota_request(db, request_id)
    grant = max(0, int(quota_tokens))

    usage = await get_or_create_usage(db, req.user_id)
    current_used = int(usage.used_tokens or 0)
    usage.quota_tokens = current_used + grant# 新配额 = 已用 + 2万
    req.status = "approved"
    await db.commit()
    await db.refresh(usage)

    app_logger.info(
        f"✅ 管理员批准提额: {req.email} → 追加 {grant} tokens"
        f"（已用 {current_used}，新配额 {usage.quota_tokens}）"
    )

    return {
        "status": "approved",
        "request_id": str(req.id),
        "email": req.email,
        **build_usage_payload(usage),
    }


@router.post("/quota-requests/{request_id}/reject")
async def reject_quota_request(
        request_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """忽略一条提额申请（仅管理员）：只改状态，不动配额。"""
    _require_admin(user)

    req = await _get_quota_request(db, request_id)
    req.status = "rejected"
    await db.commit()

    app_logger.info(f"🚫 管理员忽略提额申请: {req.email}")

    return {"status": "rejected", "request_id": str(req.id), "email": req.email}


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


@router.get("/admin/quota-dashboard", response_model=AdminQuotaDashboardResponse)
async def admin_quota_dashboard(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    管理员额度看板（按邮箱展示每位账号的用量与申请次数）。

    设计要点
    --------
    1. 出于保密只回传「邮箱」，不返回用户名/UUID；
       管理员如需进一步对单个账号加量，仍走 /quota-requests/{request_id}/approve。
    2. quota_tokens 字段已经做过 effective_quota 换算（0 自动落到全局默认），
       前端可直接拿来计算百分比，不用再理解"0 代表跟随默认"这条规则。
    3. 申请次数单独聚合（count + last_status），与用量表解耦，
       即使用户从未产生对话（request_count=0），也能看到他提过几次申请。
    """
    _require_admin(user)

    # 1) 拉所有账号 + 用量
    users_stmt = (
        select(
            User.id,
            User.email,
            TokenUsage.used_tokens,
            TokenUsage.quota_tokens,
            TokenUsage.request_count,
        )
        .select_from(User)
        .outerjoin(TokenUsage, User.id == TokenUsage.user_id)
    )
    user_rows = (await db.execute(users_stmt)).all()

    # 2) 聚合：每个账号的「申请总次数」
    app_count_stmt = (
        select(
            QuotaRequest.user_id,
            func.count(QuotaRequest.id).label("app_count"),
        )
        .group_by(QuotaRequest.user_id)
    )
    app_count_map = {
        row.user_id: int(row.app_count or 0)
        for row in (await db.execute(app_count_stmt)).all()
    }

    # 3) 取每个账号最近一次申请的状态/时间：直接按时间倒序扫一遍，
    #    数据量小（仅管理员端看板），无需 window function / DISTINCT ON。
    last_by_user: dict = {}
    last_at_by_user: dict = {}
    reqs_stmt = select(
        QuotaRequest.user_id, QuotaRequest.status, QuotaRequest.created_at
    ).order_by(QuotaRequest.created_at.desc())
    for req in (await db.execute(reqs_stmt)).all():
        if req.user_id not in last_by_user:
            last_by_user[req.user_id] = req.status
            last_at_by_user[req.user_id] = req.created_at

    items: list[dict] = []
    total_used = 0
    total_quota = 0
    for row in user_rows:
        # 用 build_usage_payload 的同款公式换算实际配额（含 0 → 全局默认）
        eff_quota = effective_quota(
            type("U", (), {"quota_tokens": row.quota_tokens})()
        )
        used = int(row.used_tokens or 0)
        total_used += used
        total_quota += eff_quota

        items.append({
            "email": row.email or "",
            "used_tokens": used,
            "quota_tokens": eff_quota,
            "remaining_tokens": max(0, eff_quota - used),
            "request_count": int(row.request_count or 0),
            "application_count": app_count_map.get(row.id, 0),
            "last_request_at": (
                last_at_by_user[row.id].isoformat()
                if last_at_by_user.get(row.id)
                else None
            ),
            "last_request_status": last_by_user.get(row.id),
        })

    # 按「用量降序」排序：把最费 token 的账号放前面看
    items.sort(key=lambda x: x["used_tokens"], reverse=True)

    return {
        "items": items,
        "total_users": len(items),
        "total_used_tokens": total_used,
        "total_quota_tokens": total_quota,
    }


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