"""Login accounts, mirrored entirely in memory.

The users table is read once at startup; every authenticated request is then
answered without touching the database (no Neon compute or transfer per
request). Writes go to the database first, then update the mirror. This
assumes a single backend process, like the rest of the app's in-memory state.
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from Config.settings import get_settings
from Database import user_repository
from Database.session import is_database_configured
from Middleware import step_logger
from Model.AuthManagementModel.user_record import UserRecord, UserSummary
from Service.AuthManagementService import password_service

_USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,32}")

_users: Dict[str, UserRecord] = {}
_ids_by_username: Dict[str, str] = {}
_write_lock = threading.Lock()
_initial_password_checks: Dict[str, Tuple[str, bool]] = {}


class UsernameTakenError(Exception):
    pass


def normalize_username(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def username_problem(username: str) -> Optional[str]:
    if not _USERNAME_PATTERN.fullmatch(username):
        return "Username must be 3–32 characters using letters, numbers, dot, dash or underscore."
    return None


def load_and_seed() -> None:
    records = user_repository.list_all() if is_database_configured() else list(_users.values())
    with _write_lock:
        _users.clear()
        _ids_by_username.clear()
        for record in records:
            _put(record)

    settings = get_settings()
    if len(settings.jwt_secret_key) < 32:
        step_logger.warn("JWT_SECRET_KEY is missing or shorter than 32 characters — set a long random value in .env.")
    if get_admin() is not None:
        return
    username = normalize_username(settings.admin_username)
    if username_problem(username) or not settings.admin_password:
        step_logger.warn("No admin account exists and ADMIN_USERNAME/ADMIN_PASSWORD in .env are missing or invalid.")
        return
    _insert(username, settings.admin_password, "admin")
    step_logger.success(f"Admin account '{username}' created.")


def _put(record: UserRecord) -> None:
    previous = _users.get(record.user_id)
    if previous is not None and previous.username != record.username:
        _ids_by_username.pop(previous.username, None)
    _users[record.user_id] = record
    _ids_by_username[record.username] = record.user_id


def _insert(username: str, plain_password: str, role: str) -> UserRecord:
    password_hash = password_service.hash_password(plain_password)
    with _write_lock:
        if username in _ids_by_username:
            raise UsernameTakenError(username)
        user_id = uuid.uuid4().hex
        if is_database_configured():
            record = user_repository.insert_user(user_id, username, password_hash, role)
        else:
            now = datetime.now(timezone.utc)
            record = UserRecord(
                user_id=user_id, username=username, password_hash=password_hash, role=role, created_at=now, updated_at=now
            )
        _put(record)
        return record


def _update(user_id: str, role_required: Optional[str], **changes) -> Optional[UserRecord]:
    with _write_lock:
        current = _users.get(user_id)
        if current is None or (role_required and current.role != role_required):
            return None
        new_username = changes.get("username")
        if new_username and new_username != current.username and new_username in _ids_by_username:
            raise UsernameTakenError(new_username)
        if is_database_configured():
            record = user_repository.update_user(user_id, role_required, **changes)
            if record is None:
                return None
        else:
            record = current.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        _put(record)
        return record


def get_by_id(user_id: str) -> Optional[UserRecord]:
    return _users.get(user_id)


def get_by_username(username: str) -> Optional[UserRecord]:
    user_id = _ids_by_username.get(normalize_username(username))
    return _users.get(user_id) if user_id else None


def get_admin() -> Optional[UserRecord]:
    return next((user for user in _users.values() if user.role == "admin"), None)


def list_employees() -> List[UserRecord]:
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted((u for u in _users.values() if u.role == "employee"), key=lambda u: u.created_at or oldest)


def create_employee(username: str, plain_password: str) -> UserRecord:
    return _insert(username, plain_password, "employee")


def update_employee(user_id: str, username: Optional[str], plain_password: Optional[str]) -> Optional[UserRecord]:
    changes = {}
    if username is not None:
        changes["username"] = username
    if plain_password is not None:
        changes["password_hash"] = password_service.hash_password(plain_password)
    return _update(user_id, "employee", **changes)


def delete_employee(user_id: str) -> bool:
    with _write_lock:
        current = _users.get(user_id)
        if current is None or current.role != "employee":
            return False
        if is_database_configured() and not user_repository.delete_employee(user_id):
            return False
        del _users[user_id]
        _ids_by_username.pop(current.username, None)
        return True


def set_password(user_id: str, plain_password: str) -> Optional[UserRecord]:
    return _update(user_id, None, password_hash=password_service.hash_password(plain_password))


def end_other_sessions(user_id: str) -> Optional[UserRecord]:
    current = _users.get(user_id)
    return _update(user_id, None, session_epoch=current.session_epoch + 1) if current else None


def session_fingerprint(user: UserRecord) -> str:
    """Changes whenever the password changes or sessions are ended, invalidating every older token."""
    return hashlib.sha256(f"{user.password_hash}|{user.session_epoch}".encode("utf-8")).hexdigest()[:32]


def _is_using_initial_password(user: UserRecord) -> bool:
    initial = get_settings().admin_password
    if user.role != "admin" or not initial:
        return False
    cached = _initial_password_checks.get(user.user_id)
    if cached is None or cached[0] != user.password_hash:
        cached = (user.password_hash, password_service.verify_password(initial, user.password_hash))
        _initial_password_checks[user.user_id] = cached
    return cached[1]


def to_summary(user: UserRecord, with_password_hint: bool = False) -> UserSummary:
    return UserSummary(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        using_initial_password=with_password_hint and _is_using_initial_password(user),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
