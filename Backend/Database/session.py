"""SQLAlchemy engine/session setup for the Postgres + pgvector database.

Only initializes when Config.settings.database_url is set. Until the final
"connect the database" step, DATABASE_URL is intentionally empty — every
module that would otherwise need a database (Service/WhatsAppDataFetchingService/property_vector_store.py
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


def _normalize_database_url(raw_url: str) -> str:
    """Neon (and most managed Postgres providers — Supabase, Render,
    Railway, Heroku) hand out a bare `postgresql://` or `postgres://`
    connection string. SQLAlchemy's default driver for that scheme is
    psycopg2, which isn't installed here — this project uses psycopg (v3)
    instead (see requirements.txt). Rewriting the scheme means the
    connection string can be pasted in exactly as the provider gives it,
    with no manual editing required."""
    if raw_url.startswith("postgresql+"):
        return raw_url
    if raw_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw_url[len("postgresql://") :]
    if raw_url.startswith("postgres://"):
        return "postgresql+psycopg://" + raw_url[len("postgres://") :]
    return raw_url


def _get_engine():
    global _engine, _session_factory
    if _engine is None:
        database_url = get_settings().database_url
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set — add it to Backend/.env to use the database.")
        # connect_timeout bounds the FIRST connection attempt only — Neon's
        # free tier suspends its compute after being idle, and waking it
        # back up on the next connection can genuinely take up to ~60s.
        # Without this, a connection that's actually failing (bad
        # credentials, network down) would hang indefinitely instead of
        # raising a clear error — see init_db()'s log line, which exists so
        # that 60s of silence doesn't look identical to a frozen process.
        _engine = create_engine(
            _normalize_database_url(database_url),
            pool_pre_ping=True,
            connect_args={"connect_timeout": 60},
        )
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
