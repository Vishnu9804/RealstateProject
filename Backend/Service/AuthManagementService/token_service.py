from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import jwt

from Config.settings import get_settings

_ALGORITHM = "HS256"
_ACCESS = "access"
_OWNER_GRANT = "owner_grant"
OWNER_GRANT_TTL_SECONDS = 10 * 60


class AuthConfigError(RuntimeError):
    pass


class AccessClaims(NamedTuple):
    user_id: str
    session_fingerprint: str


def _secret() -> str:
    secret = get_settings().jwt_secret_key
    if not secret:
        raise AuthConfigError("JWT_SECRET_KEY is not set.")
    return secret


def _encode(claims: dict, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({**claims, "iat": now, "exp": now + ttl}, _secret(), algorithm=_ALGORITHM)


def _decode(token: str, expected_type: str) -> Optional[dict]:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALGORITHM], options={"require": ["exp", "iat", "sub"]})
    except (jwt.PyJWTError, AuthConfigError):
        return None
    return claims if claims.get("typ") == expected_type else None


def create_access_token(user_id: str, session_fingerprint: str) -> str:
    ttl = timedelta(hours=get_settings().jwt_expiry_hours)
    return _encode({"typ": _ACCESS, "sub": user_id, "sfp": session_fingerprint}, ttl)


def decode_access_token(token: str) -> Optional[AccessClaims]:
    claims = _decode(token, _ACCESS)
    if not claims or not isinstance(claims.get("sfp"), str):
        return None
    return AccessClaims(user_id=claims["sub"], session_fingerprint=claims["sfp"])


def _binding(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:32]


def create_owner_grant(user_id: str, access_token: str) -> str:
    """Short-lived proof of owner verification, usable only alongside the session that earned it."""
    claims = {"typ": _OWNER_GRANT, "sub": user_id, "bnd": _binding(access_token)}
    return _encode(claims, timedelta(seconds=OWNER_GRANT_TTL_SECONDS))


def verify_owner_grant(grant: str, user_id: str, access_token: str) -> bool:
    claims = _decode(grant, _OWNER_GRANT)
    if not claims or claims["sub"] != user_id:
        return False
    return hmac.compare_digest(str(claims.get("bnd", "")), _binding(access_token))
