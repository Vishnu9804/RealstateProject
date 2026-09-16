"""Sign-in, the caller's own account, owner verification and owner password recovery.

Credential mistakes return 400, never 401 — the frontend treats 401 as
"session ended" and signs the user out.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from Model.AuthManagementModel.user_record import (
    LoginResult,
    OwnerVerificationGrant,
    OwnerVerificationStatus,
    RecoveryResetResult,
    UserRecord,
    UserSummary,
    VerificationCodeResult,
)
from Service.AuthManagementService import (
    login_throttle,
    owner_verification_service,
    password_service,
    token_service,
    user_store,
)
from Service.AuthManagementService.auth_dependencies import (
    bearer_token,
    get_current_user,
    require_admin,
    require_owner_verification,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CODE = "That code isn't right, or it has expired."


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class OwnerVerificationConfirmRequest(BaseModel):
    code: Optional[str] = Field(default=None, max_length=12)
    password: Optional[str] = Field(default=None, max_length=256)


class RecoveryResetRequest(BaseModel):
    code: str = Field(max_length=12)
    new_password: str = Field(max_length=256)


def _session(user: UserRecord) -> LoginResult:
    try:
        token = token_service.create_access_token(user.user_id, user_store.session_fingerprint(user))
    except token_service.AuthConfigError as exc:
        raise HTTPException(status_code=503, detail="Sign-in isn't configured on the server (JWT_SECRET_KEY).") from exc
    return LoginResult(access_token=token, user=user_store.to_summary(user, with_password_hint=True))


def _throttled(key: str) -> None:
    wait = login_throttle.retry_after_seconds(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {max(1, round(wait / 60))} minute(s).",
            headers={"Retry-After": str(wait)},
        )


@router.post("/login", response_model=LoginResult)
def login(body: LoginRequest) -> LoginResult:
    username = user_store.normalize_username(body.username)
    _throttled(username)
    user = user_store.get_by_username(username)
    if user is None:
        password_service.burn_verification_time(body.password)
    if user is None or not password_service.verify_password(body.password, user.password_hash):
        login_throttle.record_failure(username)
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    login_throttle.clear(username)
    return _session(user)


@router.get("/me", response_model=UserSummary)
def get_me(current_user: UserSummary = Depends(get_current_user)) -> UserSummary:
    return user_store.to_summary(user_store.get_by_id(current_user.user_id), with_password_hint=True)


@router.post("/refresh", response_model=LoginResult)
def refresh_session(current_user: UserSummary = Depends(get_current_user)) -> LoginResult:
    """Swaps a still-valid token for a fresh one, so a user who keeps working
    is never signed out when their token's lifetime runs out. Only a token
    that passes get_current_user can be swapped — an expired one, or one
    invalidated by a password change or "end other sessions", gets the usual
    401 — and the new token carries the same session fingerprint, so ending
    sessions still ends this one too."""
    user = user_store.get_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Your session has expired or is no longer valid. Please sign in again.")
    return _session(user)


@router.patch("/me/password", response_model=LoginResult)
def change_own_password(
    body: ChangeOwnPasswordRequest, current_user: UserSummary = Depends(require_owner_verification)
) -> LoginResult:
    user = user_store.get_by_id(current_user.user_id)
    if not password_service.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    problem = password_service.password_problem(body.new_password, user.username)
    if problem is None and body.new_password == body.current_password:
        problem = "Choose a password different from your current one."
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    updated = user_store.set_password(user.user_id, body.new_password)
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return _session(updated)


@router.post("/me/end-other-sessions", response_model=LoginResult)
def end_other_sessions(current_user: UserSummary = Depends(require_admin)) -> LoginResult:
    updated = user_store.end_other_sessions(current_user.user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return _session(updated)


@router.get("/owner-verification", response_model=OwnerVerificationStatus, dependencies=[Depends(require_admin)])
def get_owner_verification_status() -> OwnerVerificationStatus:
    return owner_verification_service.status()


@router.post("/owner-verification/code", response_model=VerificationCodeResult, dependencies=[Depends(require_admin)])
def request_owner_verification_code() -> VerificationCodeResult:
    return owner_verification_service.request_code(owner_verification_service.STEP_UP)


@router.post("/owner-verification/confirm", response_model=OwnerVerificationGrant)
def confirm_owner_verification(
    body: OwnerVerificationConfirmRequest, request: Request, current_user: UserSummary = Depends(require_admin)
) -> OwnerVerificationGrant:
    throttle_key = f"owner-verification:{current_user.user_id}"
    _throttled(throttle_key)
    if owner_verification_service.status().method == "whatsapp":
        ok = owner_verification_service.verify_code(owner_verification_service.STEP_UP, body.code or "")
        detail = _BAD_CODE
    else:
        user = user_store.get_by_id(current_user.user_id)
        ok = password_service.verify_password(body.password or "", user.password_hash)
        detail = "That password is incorrect."
    if not ok:
        login_throttle.record_failure(throttle_key)
        raise HTTPException(status_code=400, detail=detail)
    login_throttle.clear(throttle_key)
    return OwnerVerificationGrant(
        verification_token=token_service.create_owner_grant(current_user.user_id, bearer_token(request)),
        expires_in_seconds=token_service.OWNER_GRANT_TTL_SECONDS,
    )


@router.post("/recovery/code", response_model=VerificationCodeResult)
def request_recovery_code() -> VerificationCodeResult:
    if user_store.get_admin() is None:
        return VerificationCodeResult(status="not_configured")
    return owner_verification_service.request_code(owner_verification_service.RECOVERY)


@router.post("/recovery/reset", response_model=RecoveryResetResult)
def reset_with_recovery_code(body: RecoveryResetRequest) -> RecoveryResetResult:
    admin = user_store.get_admin()
    if admin is None or owner_verification_service.status().method != "whatsapp":
        raise HTTPException(status_code=400, detail="Password reset by WhatsApp isn't set up on this server.")
    problem = password_service.password_problem(body.new_password, admin.username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if not owner_verification_service.verify_code(owner_verification_service.RECOVERY, body.code):
        raise HTTPException(status_code=400, detail=_BAD_CODE)
    user_store.set_password(admin.user_id, body.new_password)
    login_throttle.clear(admin.username)
    return RecoveryResetResult(username=admin.username)
