from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

UserRole = Literal["admin", "employee"]


class UserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str
    role: UserRole
    session_epoch: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserSummary(BaseModel):
    user_id: str
    username: str
    role: UserRole
    using_initial_password: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class LoginResult(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary


class OwnerVerificationStatus(BaseModel):
    method: Literal["whatsapp", "password"]
    available: bool
    reason: Optional[Literal["no_whatsapp_connection", "invalid_owner_phone"]] = None
    phone_hint: Optional[str] = None


class VerificationCodeResult(BaseModel):
    status: Literal["sent", "cooldown", "unavailable", "not_configured"]
    retry_after_seconds: int = 0
    phone_hint: Optional[str] = None


class OwnerVerificationGrant(BaseModel):
    verification_token: str
    expires_in_seconds: int


class RecoveryResetResult(BaseModel):
    username: str
