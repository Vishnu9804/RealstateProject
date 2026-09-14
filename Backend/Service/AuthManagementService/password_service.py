from __future__ import annotations

from typing import Optional

import bcrypt

MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_BYTES = 72  # bcrypt ignores anything beyond this
_dummy_hash: Optional[bytes] = None


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8")[:_MAX_PASSWORD_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        return False


def burn_verification_time(plain_password: str) -> None:
    """Spends the same bcrypt time as a real check so unknown usernames can't be detected by timing."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"timing-placeholder", bcrypt.gensalt())
    bcrypt.checkpw(plain_password.encode("utf-8")[:_MAX_PASSWORD_BYTES], _dummy_hash)


def password_problem(password: str, username: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return "Password is too long (72 bytes at most)."
    if password.strip().lower() == username.strip().lower():
        return "Password can't be the same as the username."
    return None
