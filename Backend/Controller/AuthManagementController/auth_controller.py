"""Sign-in, the caller's own account, and owner verification for staff-login
changes.

There is no "forgot password" flow: the admin account's password starts out
as ADMIN_PASSWORD from .env and can only ever be changed by signing in and
entering the current password. If it's ever truly forgotten, it must be
reset directly in the database or by changing .env before an admin account
exists.

Credential mistakes return 400, never 401 — the frontend treats 401 as
"session ended" and signs the user out.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from Model.AuthManagementModel.user_record import LoginResult, OwnerVerificationGrant, UserRecord, UserSummary
from Service.AuthManagementService import login_throttle, password_service, token_service, user_store
from Service.AuthManagementService.auth_dependencies import bearer_token, get_current_user, require_admin

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class OwnerVerificationConfirmRequest(BaseModel):
    password: str = Field(max_length=256)


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
def change_own_password(body: ChangeOwnPasswordRequest, current_user: UserSummary = Depends(require_admin)) -> LoginResult:
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


@router.post("/owner-verification/confirm", response_model=OwnerVerificationGrant)
def confirm_owner_verification(
    body: OwnerVerificationConfirmRequest, request: Request, current_user: UserSummary = Depends(require_admin)
) -> OwnerVerificationGrant:
    """Proves an admin, adding or changing a staff login, really knows the
    admin password. This is the only step-up check in the app — there is no
    OTP/WhatsApp option — so it always checks the caller's own password."""
    throttle_key = f"owner-verification:{current_user.user_id}"
    _throttled(throttle_key)
    user = user_store.get_by_id(current_user.user_id)
    if not password_service.verify_password(body.password, user.password_hash):
        login_throttle.record_failure(throttle_key)
        raise HTTPException(status_code=400, detail="That password is incorrect.")
    login_throttle.clear(throttle_key)
    return OwnerVerificationGrant(
        verification_token=token_service.create_owner_grant(current_user.user_id, bearer_token(request)),
        expires_in_seconds=token_service.OWNER_GRANT_TTL_SECONDS,
    )
