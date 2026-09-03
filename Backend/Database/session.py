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
        _engine = create_engine(_normalize_database_url(database_url), pool_pre_ping=True)
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
    main.py's lifespan).

    create_all only creates whole tables that are missing — it never adds a
    column to a `properties` table that already exists from a previous
    deploy. The ALTER TABLE statements below are the lightweight stand-in
    for a real migration tool (this project has none): each one is
    idempotent (IF NOT EXISTS) and nullable, so it's safe to run on every
    startup and never touches existing rows/columns."""
    from sqlalchemy import text

    from Database.models import Base

    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS price_per_unit_text VARCHAR"))
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS price_per_unit_amount_inr FLOAT")
        )
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS carpet_area_unit VARCHAR"))
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS record_id VARCHAR"))
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS listing_type VARCHAR NOT NULL DEFAULT 'Sale'")
        )
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT false")
        )
        # One-time backfill for rows written before needs_review existed,
        # when "needs_review" was itself a review_status value rather than
        # its own column: carry that meaning over to the new column and
        # collapse review_status back down to the two-value "accepted"/
        # "outsider" it's now restricted to. Idempotent — after the first
        # run no row matches this WHERE clause again.
        connection.execute(
            text(
                "UPDATE properties SET needs_review = true, review_status = 'accepted' "
                "WHERE review_status = 'needs_review'"
            )
        )
