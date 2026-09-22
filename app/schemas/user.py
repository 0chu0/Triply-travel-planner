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


class QuotaRequestCreate(BaseModel):
    """提额申请"""
    reason: str = Field(default="", max_length=500)