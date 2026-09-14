"""Postgres persistence for login accounts. Every call is a single statement;
reads happen once at startup (user_store keeps the table in memory)."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import delete, func, insert, select, update

from Database.session import get_session
from Database.user_models import UserRow
from Model.AuthManagementModel.user_record import UserRecord

_table = UserRow.__table__
_COLUMNS = (
    _table.c.user_id,
    _table.c.username,
    _table.c.password_hash,
    _table.c.role,
    _table.c.session_epoch,
    _table.c.created_at,
    _table.c.updated_at,
)


def _record(row) -> UserRecord:
    return UserRecord(**row._mapping)


def list_all() -> List[UserRecord]:
    with get_session() as session:
        return [_record(row) for row in session.execute(select(*_COLUMNS)).all()]


def insert_user(user_id: str, username: str, password_hash: str, role: str) -> UserRecord:
    stmt = (
        insert(_table)
        .values(user_id=user_id, username=username, password_hash=password_hash, role=role, session_epoch=0)
        .returning(*_COLUMNS)
    )
    with get_session() as session:
        return _record(session.execute(stmt).one())


def update_user(user_id: str, role_required: Optional[str], **changes) -> Optional[UserRecord]:
    stmt = update(_table).where(_table.c.user_id == user_id)
    if role_required:
        stmt = stmt.where(_table.c.role == role_required)
    stmt = stmt.values(**changes, updated_at=func.now()).returning(*_COLUMNS)
    with get_session() as session:
        row = session.execute(stmt).one_or_none()
    return _record(row) if row is not None else None


def delete_employee(user_id: str) -> bool:
    stmt = delete(_table).where(_table.c.user_id == user_id, _table.c.role == "employee")
    with get_session() as session:
        return session.execute(stmt).rowcount == 1
