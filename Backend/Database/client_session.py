"""SQLAlchemy engine/session setup for the separate client database (Neon
Postgres) used by whatsappInquiryHandling — entirely independent of
Database/session.py, the property-listing database used by
whatsappDataFetching. A second, self-contained module rather than
generalizing session.py to take a URL parameter: that would mean touching a
file the existing feature depends on for a change only this feature needs.

Only initializes when Config.settings.client_database_url is set — until
then, Service/WhatsAppInquiryHandlingService/client_store.py falls back to
an in-memory dict, exactly like property_vector_store.py already does for
DATABASE_URL.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from Config.settings import get_settings

_engine = None
_session_factory: Optional[sessionmaker] = None


def is_client_database_configured() -> bool:
    return bool(get_settings().client_database_url)


def _normalize_database_url(raw_url: str) -> str:
    """Same rewrite as Database/session.py's helper — Neon (and most
    managed Postgres providers) hand out a bare postgresql://, but this
    project uses the psycopg (v3) driver, not psycopg2."""
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
        database_url = get_settings().client_database_url
        if not database_url:
            raise RuntimeError(
                "CLIENT_DATABASE_URL is not set — add it to Backend/.env to use the client database."
            )
        _engine = create_engine(_normalize_database_url(database_url), pool_pre_ping=True)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def get_client_session() -> Iterator[Session]:
    """One session per call, committed on success and rolled back on any
    exception — every repository function is a single `with
    get_client_session()` block, so a client record is never left
    half-written."""
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


def init_client_db() -> None:
    """Creates the clients table if it doesn't already exist. Safe to call
    on every startup — a no-op once the schema is in place. Only called
    when is_client_database_configured() is True (see main.py's lifespan).
    No pgvector extension needed here — unlike the property database,
    client records carry no embeddings."""
    from Database.client_models import ClientBase

    engine = _get_engine()
    ClientBase.metadata.create_all(engine)
