"""SQLAlchemy engine/session setup for the Postgres + pgvector database.

Only initializes when Config.settings.database_url is set. Until the final
"connect the database" step, DATABASE_URL is intentionally empty — every
module that would otherwise need a database (Service/property_vector_store.py
and the *_settings services) checks `is_database_configured()` and falls
back to the in-memory behavior they've had since their own step, unchanged.
Nothing in the app requires a database to exist in order to run.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from Config.settings import get_settings

_engine = None
_session_factory: Optional[sessionmaker] = None


def is_database_configured() -> bool:
    return bool(get_settings().database_url)


def _get_engine():
    global _engine, _session_factory
    if _engine is None:
        database_url = get_settings().database_url
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set — add it to Backend/.env to use the database.")
        _engine = create_engine(database_url, pool_pre_ping=True)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    """One session per call, committed on success and rolled back on any
    exception — every repository function is a single `with get_session()`
    block, so nothing here is ever left half-written."""
    _get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Enables the pgvector extension and creates any tables that don't
    already exist. Safe to call on every startup — a no-op once the schema
    is in place. Only called when is_database_configured() is True (see
    main.py's lifespan)."""
    from sqlalchemy import text

    from Database.models import Base

    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
