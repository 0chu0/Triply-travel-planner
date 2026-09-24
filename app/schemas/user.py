"""
用户相关的 Pydantic 模型
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class UserRegister(BaseModel):
    """用户注册"""
    username: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    """用户登录"""
    username: str
    password: str


class UserResponse(BaseModel):
    """用户信息响应"""
    id: uuid.UUID
    username: str
    email: str
    preferences: Optional[Dict[str, Any]] = None
    created_at: datetime
    # 是否管理员：仅用于让前端决定要不要显示「消息通知」入口，不承担鉴权职责
    # （真正的鉴权仍需后端每个接口自己校验，见 users.py 的 _require_admin）
    is_admin: bool = False

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """令牌响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse  #from_attributes = True的作用是：允许这个 Pydantic 模型直接读取 SQLAlchemy 的数据库对象。


class UsageResponse(BaseModel):
    """Token 用量 / 配额响应"""
    used_tokens: int
    quota_tokens: int
    remaining_tokens: int
    percent: float
    request_count: int
    exceeded: bool
    contact: str = ""
    # 当前账号是否已有「待处理」的提额申请：
    # - true  → 前端按钮置灰显示「申请已提交」，避免重复点击造成无效申请
    # - false → 按钮可用「申请更多额度」，管理员通过/忽略后即可再次提交
    has_pending_request: bool = False


class QuotaRequestCreate(BaseModel):
    """提额申请"""
    reason: str = Field(default="", max_length=500)


class AdminQuotaDashboardRow(BaseModel):
    """管理员额度看板单行：每位账号一行用量摘要"""
    email: str
    used_tokens: int
    quota_tokens: int  # 实际生效配额（0 时回落到全局默认，这里已换算成绝对值）
    remaining_tokens: int
    request_count: int  # 对话轮数（来自 token_usage.request_count）
    application_count: int  # 提额申请次数（来自 quota_request 行数）
    last_request_at: Optional[str] = None
    last_request_status: Optional[str] = None  # pending / approved / rejected


class AdminQuotaDashboardResponse(BaseModel):
    """管理员额度看板整体响应"""
    items: list[AdminQuotaDashboardRow]
    total_users: int
    total_used_tokens: int
    total_quota_tokens: int