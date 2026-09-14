"""SQLAlchemy ORM model for the AuthManagement feature's login accounts —
lives on the same shared Base as Database/models.py's PropertyRow etc. (same
DATABASE_URL/engine, same create_all in Database/session.py's init_db): a
login account isn't tied to the property or client schema specifically, so
it goes on the one general-purpose Base rather than ClientBase.

Importing this module is what registers the `users` table on that shared
Base — see the import inside init_db().
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class UserRow(Base):
    """One login account. Only ever created through
    Database/user_repository.py — never via the request body's `role`
    directly (see Controller/AuthManagementController/user_controller.py's
    create_employee, which hardcodes role="employee"), so this table can
    never grow a second admin through the API surface."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)

    username: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # "admin" | "employee" — plain String rather than a DB-level ENUM, same
    # choice this project already makes for other small closed-set text
    # fields (e.g. PropertyRow.listing_type), so adding a role later never
    # needs a migration to widen an enum type.
    role: Mapped[str] = mapped_column(String, nullable=False)
    # Bumped by "sign out other devices"; part of every token's session fingerprint.
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
