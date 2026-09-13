"""get_current_user: any signed-in account (router-level in main.py).
require_admin: destructive actions and account management.
require_owner_verification: creating/changing credentials (428 until the owner confirms)."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request

from Model.AuthManagementModel.user_record import UserSummary
from Service.AuthManagementService import token_service, user_store

OWNER_VERIFICATION_HEADER = "X-Owner-Verification"


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def get_current_user(request: Request) -> UserSummary:
    token = bearer_token(request)
    claims = token_service.decode_access_token(token) if token else None
    user = user_store.get_by_id(claims.user_id) if claims else None
    if user is None or not hmac.compare_digest(claims.session_fingerprint, user_store.session_fingerprint(user)):
        raise HTTPException(status_code=401, detail="Your session has expired or is no longer valid. Please sign in again.")
    return user_store.to_summary(user)


def require_admin(current_user: UserSummary = Depends(get_current_user)) -> UserSummary:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only the owner (admin) can do this.")
    return current_user


def require_owner_verification(request: Request, current_user: UserSummary = Depends(require_admin)) -> UserSummary:
    grant = request.headers.get(OWNER_VERIFICATION_HEADER)
    token = bearer_token(request)
    if not grant or not token or not token_service.verify_owner_grant(grant, current_user.user_id, token):
        raise HTTPException(status_code=428, detail="Owner verification is required for this change.")
    return current_user
