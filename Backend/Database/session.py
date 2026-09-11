"""SQLAlchemy engine/session setup for the single Postgres + pgvector
database the whole app shares — both the property-listing tables
(whatsappDataFetching) and the client-records tables (whatsappInquiryHandling,
see Database/client_session.py, which reuses this module's engine/session
rather than opening a second connection to the same database).

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
    already exist — for BOTH features that use this database: the
    property-listing tables (Database/models.py, Database/landing_page_models.py)
    and the client-records tables (Database/client_models.py,
    Database/client_property_match_models.py, owned by whatsappInquiryHandling).
    Safe to call on every startup — a no-op once the schema is in place.
    Only called when is_database_configured() is True (see main.py's
    lifespan).

    This is deliberately the ONLY place in the project that runs
    CREATE EXTENSION / create_all / these ALTER TABLE statements. It used to
    be split across this module and Database/client_session.py, each opening
    its own connection and running its own `CREATE EXTENSION IF NOT EXISTS
    vector` — since both pointed at the same physical database and were
    kicked off concurrently (main.py's lifespan), Postgres's IF NOT EXISTS
    check isn't safe against true concurrency, so one of the two would
    intermittently lose a race and raise a duplicate-key error on a fresh
    database. Running everything from one function, sequentially, removes
    the race by construction rather than by catching the error.

    create_all only creates whole tables that are missing — it never adds a
    column to a `properties` table that already exists from a previous
    deploy. The ALTER TABLE statements below are the lightweight stand-in
    for a real migration tool (this project has none): each one is
    idempotent (IF NOT EXISTS) and nullable, so it's safe to run on every
    startup and never touches existing rows/columns."""
    from sqlalchemy import text

    from Database.models import Base

    # Imported for its import side effect only: defining LandingLeadRow is
    # what registers the landing_page_leads table on the Base above, and
    # create_all can only create tables it has been told about. Nothing
    # else in this module's startup path imports the LandingPage feature,
    # so without this line that table is silently never created.
    from Database import landing_page_models  # noqa: F401

    # Same import-for-side-effect reasoning, for the broker-requirements
    # table: defining BrokerRequirementRow is what registers
    # `broker_requirements` on the Base above, and create_all can only
    # create tables it has been told about. Nothing else on this startup
    # path imports the requirement pipeline, so without this line that
    # table is silently never created.
    from Database import broker_requirement_models  # noqa: F401

    # Same import-for-side-effect reasoning, for the sold-out properties
    # table: defining SoldOutPropertyRow is what registers
    # `soldout_properties` on the Base above, and create_all can only create
    # tables it has been told about. Stated here explicitly rather than left
    # to the fact that main.py's import graph happens to reach that model
    # too — this function's correctness must not depend on what some other
    # module imports. No ALTER TABLE companion is needed below: this is a
    # brand-new table, which is exactly the case create_all handles on its
    # own.
    from Database import soldout_property_models  # noqa: F401

    # Same import-for-side-effect reasoning, for the client-records tables:
    # ClientBase is a second declarative base (kept separate from Base so
    # the two features' models can never accidentally collide), but both
    # sets of tables live in this same database and are created here, in
    # this one function, so table creation never has a second call site.
    from Database.client_models import ClientBase
    from Database.client_property_match_models import ClientPropertyMatchRow  # noqa: F401

    # Same import-for-side-effect reasoning again, for the AgentManagement
    # feature's tables — AgentRow and AgentVisitRow also live on ClientBase
    # (see Database/agent_models.py's own docstring on why).
    from Database.agent_assignment_models import AgentAssignmentRow  # noqa: F401
    from Database.agent_models import AgentRow  # noqa: F401
    from Database.agent_visit_models import AgentVisitRow  # noqa: F401
    from Database.manual_property_models import ManualPropertyRow  # noqa: F401
    from Service.WhatsAppDataFetchingService.embedding_service import EMBEDDING_DIMENSIONS

    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    ClientBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE clients ADD COLUMN IF NOT EXISTS requirement_embedding vector({EMBEDDING_DIMENSIONS})")
        )
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
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS instagram_reel_url VARCHAR"))
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS instagram_media_pk VARCHAR"))
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS image_urls JSON NOT NULL DEFAULT '[]'::json")
        )
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS on_landing_page BOOLEAN NOT NULL DEFAULT false")
        )
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS landing_page_updated_at TIMESTAMPTZ")
        )
        connection.execute(text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS qualified_at TIMESTAMPTZ"))
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        )
    with engine.begin() as connection:
        # One-time move of the WhatsApp message fields (group_name,
        # chat_type, sender_name, sender_saved_name, sender_phone,
        # message_text, message_timestamp) off `properties` and onto the new
        # `whatsapp_messages` table (Database/models.py's WhatsAppMessageRow)
        # — before this, a WhatsApp message that produced several properties
        # (see StructuredProperty.record_id's own comment) had its full text
        # stored once per property instead of once per message.
        #
        # Gated on message_text still existing on `properties` rather than
        # an IF NOT EXISTS on a single statement (this needs several
        # statements done together): the first run backfills
        # whatsapp_messages from the old columns, points the existing rows'
        # source_message_id at it via a real foreign key, then drops the
        # now-redundant columns; every run after that finds message_text
        # already gone and does nothing. create_all above already created
        # whatsapp_messages (and, for a brand new database, the foreign key
        # too) before this block runs.
        still_has_old_columns = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = 'message_text'"
            )
        ).first()
        if still_has_old_columns:
            connection.execute(
                text(
                    """
                    INSERT INTO whatsapp_messages
                        (id, group_name, chat_type, sender_name, sender_saved_name,
                         sender_phone, message_text, message_timestamp)
                    SELECT DISTINCT ON (source_message_id)
                        source_message_id, group_name, chat_type, sender_name, sender_saved_name,
                        sender_phone, message_text, message_timestamp
                    FROM properties
                    ORDER BY source_message_id, id
                    ON CONFLICT (id) DO NOTHING
                    """
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE properties ADD CONSTRAINT properties_source_message_id_fkey "
                    "FOREIGN KEY (source_message_id) REFERENCES whatsapp_messages(id)"
                )
            )
            connection.execute(text("ALTER TABLE properties DROP COLUMN group_name"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN chat_type"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN sender_name"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN sender_saved_name"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN sender_phone"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN message_text"))
            connection.execute(text("ALTER TABLE properties DROP COLUMN message_timestamp"))
    with engine.begin() as connection:
        # Content fingerprint of the message text, added to whatsapp_messages
        # after that table already existed (create_all only creates whole
        # tables, never adds a column to one that's already there — so this
        # ALTER is what upgrades an existing database, and the CREATE INDEX
        # is what gives the pre-LLM duplicate check its single indexed
        # lookup instead of a table scan). Both idempotent; both no-ops on a
        # brand new database, where create_all already made them from
        # WhatsAppMessageRow's own definition.
        connection.execute(
            text("ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS text_fingerprint VARCHAR(64)")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_text_fingerprint "
                "ON whatsapp_messages (text_fingerprint)"
            )
        )
        # Retiring the vector-search duplicate-detection stage. Every
        # needs_review flag in an existing database was set by it and means
        # "this might duplicate that property" — which is NOT what the flag
        # means any more (it now means "almost nothing could be extracted
        # from this property", see StructuredProperty.needs_review). Leaving
        # those rows flagged would be actively wrong: they hold full
        # details, so they would sit in a manual-completion queue with
        # nothing to complete, AND be withheld from client matching (see
        # matching_service._is_matchable) — hiding good listings from
        # clients until someone cleared each one by hand. So the flag is
        # reset and the duplicate sentence is stripped back out of
        # review_notes, leaving any outsider reason that was appended to it
        # intact. The properties themselves are untouched and stay in
        # whichever tab (Main/Outsider) review_status already says.
        #
        # Gated on duplicate_of_record_id still existing, which is true
        # exactly once — before this migration drops it just below. That
        # matters: an ungated version of this UPDATE would re-run on every
        # startup and silently clear the flags the NEW logic had correctly
        # set since.
        had_duplicate_detection = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = 'duplicate_of_record_id'"
            )
        ).first()
        if had_duplicate_detection:
            connection.execute(
                text(
                    """
                    UPDATE properties
                    SET needs_review = false,
                        review_notes = NULLIF(btrim(regexp_replace(
                            COALESCE(review_notes, ''),
                            '(\\s*\\|\\s*)?(High-confidence duplicate|Possible duplicate) of message.*$',
                            ''
                        )), '')
                    WHERE needs_review = true
                    """
                )
            )
            connection.execute(text("ALTER TABLE properties DROP COLUMN duplicate_of_record_id"))
            # The tuned thresholds that stage kept in app_settings — nothing
            # reads this key any more, and its endpoints are gone.
            connection.execute(
                text("DELETE FROM app_settings WHERE key = 'duplicate_detection_settings'")
            )
        # The per-field embedding vectors existed only for that stage's
        # field-by-field semantic comparison. The whole-property `embedding`
        # column stays — client-property match scoring still uses it.
        # IF EXISTS, so this is a no-op after the first run and on a
        # database that never had it.
        connection.execute(text("ALTER TABLE properties DROP COLUMN IF EXISTS field_embeddings"))
    with engine.begin() as connection:
        # AgentManagement feature: which agent (if any) is handling this
        # client's site visit, and whether the WhatsApp hand-off messages
        # were ever sent for them.
        connection.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS assigned_agent_id VARCHAR"))
        connection.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS handoff_sent_at TIMESTAMPTZ"))
        # Which property a completed visit was actually about — added after
        # agent_visits already existed in production, so both are nullable
        # for rows written before this column existed.
        connection.execute(text("ALTER TABLE agent_visits ADD COLUMN IF NOT EXISTS property_record_id VARCHAR"))
        connection.execute(text("ALTER TABLE agent_visits ADD COLUMN IF NOT EXISTS property_label VARCHAR"))
        # Budget snapshot, added once "Mark as still active" needed
        # something to recreate the assignment's budget from — same
        # nullable-for-old-rows treatment as the two columns just above.
        connection.execute(text("ALTER TABLE agent_visits ADD COLUMN IF NOT EXISTS budget_min_inr FLOAT"))
        connection.execute(text("ALTER TABLE agent_visits ADD COLUMN IF NOT EXISTS budget_max_inr FLOAT"))
    with engine.begin() as connection:
        # When a property's Instagram reel link was last set/changed — what
        # the poller's "most recently linked reels" list orders by (see
        # PropertyRow.instagram_reel_url_updated_at and
        # property_vector_store.get_recent_instagram_reel_properties).
        connection.execute(
            text("ALTER TABLE properties ADD COLUMN IF NOT EXISTS instagram_reel_url_updated_at TIMESTAMPTZ")
        )
        # Which linked WhatsApp number a broker requirement came in on —
        # see BrokerRequirementRow.source_connection_id. Added after the
        # table already existed, so this ALTER (not create_all) is what
        # upgrades an existing database; nullable, so existing rows keep
        # meaning "unknown", which is exactly the fall-back-to-any-listening
        # -connection case the sender already handles.
        connection.execute(
            text("ALTER TABLE broker_requirements ADD COLUMN IF NOT EXISTS source_connection_id VARCHAR")
        )
        # Per-client watermark for the daily incremental rescore — see
        # ClientRow.matches_computed_at. Left NULL for existing clients,
        # which correctly means "never scored incrementally yet", so each
        # one gets exactly one full pass before incremental runs take over.
        connection.execute(
            text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS matches_computed_at TIMESTAMPTZ")
        )
        # Public-form abuse guards -- see ClientRow.requirement_submission_count
        # and InstagramContactRow's copy of it. NOT NULL DEFAULT 0 rather
        # than nullable: every existing row must read as "no submissions
        # counted yet" and keep its full allowance, and a default of 0 says
        # exactly that without a backfill pass.
        connection.execute(
            text(
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
                "requirement_submission_count INTEGER NOT NULL DEFAULT 0"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE instagram_contacts ADD COLUMN IF NOT EXISTS "
                "requirement_submission_count INTEGER NOT NULL DEFAULT 0"
            )
        )
        # The stored, indexed E.164 number on landing-page leads -- see
        # LandingLeadRow.phone_e164. The index is what turns the repeat-
        # enquiry check into one indexed probe instead of a table scan; both
        # statements are idempotent and no-ops on a fresh database, where
        # create_all already built them from the model.
        connection.execute(text("ALTER TABLE landing_page_leads ADD COLUMN IF NOT EXISTS phone_e164 VARCHAR"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_landing_page_leads_phone_property "
                "ON landing_page_leads (phone_e164, property_record_id)"
            )
        )
        # One-time backfill for rows that already had a reel link before this
        # column existed. qualified_at is the closest thing already recorded
        # ("when this property most recently gained a photo or a reel"), with
        # updated_at as the fallback for rows predating that too. Idempotent:
        # the IS NULL clause matches nothing on any later run, so a value the
        # app has since written for a real edit is never overwritten.
        connection.execute(
            text(
                """
                UPDATE properties
                SET instagram_reel_url_updated_at = COALESCE(qualified_at, updated_at)
                WHERE instagram_reel_url IS NOT NULL
                  AND instagram_reel_url <> ''
                  AND instagram_reel_url_updated_at IS NULL
                """
            )
        )

    # Fills text_fingerprint for message rows that predate that column —
    # deliberately in Python rather than as SQL above, so the stored value is
    # produced by the exact same function the runtime check computes with
    # (see property_repository.backfill_message_fingerprints for why an
    # equivalent-looking SQL expression is not good enough). Imported here,
    # not at module scope, to keep this module's import graph as narrow as
    # the rest of init_db's imports.
    from Database import property_repository
    from Middleware import step_logger

    filled = property_repository.backfill_message_fingerprints()
    if filled:
        step_logger.info(
            f"Backfilled content fingerprints for {filled} stored WhatsApp message(s) — they can now be "
            "recognised by the pre-LLM exact-duplicate check."
        )

    # Same reasoning again for landing-page leads: phone_e164 is produced by
    # Service/WhatsAppInquiryHandlingService/phone_utils.normalize_phone, and
    # only that function's output is comparable with the value the repeat-
    # enquiry check looks up. One bounded pass over the rows that have no
    # value yet; after the first run it matches nothing, so later startups
    # cost a single indexless-but-tiny "WHERE phone_e164 IS NULL" probe.
    from Database import landing_lead_repository

    stamped = landing_lead_repository.backfill_phone_e164()
    if stamped:
        step_logger.info(
            f"Backfilled the canonical phone number on {stamped} landing-page lead(s) — repeat-enquiry "
            "checks for them are now a single indexed lookup."
        )
