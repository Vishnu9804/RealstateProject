"""Storage abstraction for Instagram-only contacts — mirrors Service/
WhatsAppInquiryHandlingService/client_store.py's role and shape exactly.
Callers never know or care which backend is active underneath:

  - DATABASE_URL unset: falls back to an in-memory dict, keyed by
    Instagram user id.
  - DATABASE_URL set: delegates to Database/instagram_contact_repository.py.

Also owns the processed-event idempotency guard (is_event_processed/
mark_event_processed) — small enough, and tied closely enough to the same
database/in-memory-fallback split, that a separate store module for it
would just be indirection.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Set

from Database import instagram_contact_repository
from Database.client_session import is_client_database_configured
from Middleware import step_logger
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord

# In-memory fallback only — untouched whenever the client database is configured.
_contacts: Dict[str, InstagramContactRecord] = {}
_processed_events: Set[str] = set()

# --- the processed-event guard's in-memory index (database mode) ----------
#
# A bounded, insertion-ordered set of event keys already known to be
# recorded. It answers is_event_processed without a query, which matters
# because that question is asked about every comment and every DM message
# Instagram returns, on every poll cycle, for the life of the process —
# Instagram keeps returning the same recent items long after they have been
# answered, so almost every one of those questions used to be a database
# round trip re-confirming a "yes" from hours or days earlier.
#
# Two properties make this safe to trust, and both are load-bearing:
#
#   - It only ever caches "yes, processed". A key that is NOT here is not
#     assumed to be new: the database is still asked, and remains the
#     authority. So an evicted or never-loaded key costs one query, never a
#     duplicate reply.
#   - Nothing in this application ever deletes an InstagramProcessedEventRow
#     (see Database/instagram_contact_repository.py — insert and read only),
#     so a cached "yes" cannot become wrong. If deletion is ever added, this
#     cache must be invalidated there.
#
# The bound matters as much as the cache: an unbounded set on a process that
# runs for months would grow with every event ever seen. At the limit the
# oldest entry is dropped, and the only cost of dropping one is a single
# query if that old event ever resurfaces.
_PROCESSED_CACHE_LIMIT = 10_000
_processed_cache: "OrderedDict[str, None]" = OrderedDict()
_processed_cache_lock = threading.Lock()
_processed_cache_primed = False


def _remember_processed_locked(event_key: str) -> None:
    _processed_cache[event_key] = None
    # Assigning an existing key does not reorder it, so the freshness that
    # eviction below depends on has to be stated explicitly.
    _processed_cache.move_to_end(event_key)
    while len(_processed_cache) > _PROCESSED_CACHE_LIMIT:
        _processed_cache.popitem(last=False)


def _remember_processed(event_key: str) -> None:
    with _processed_cache_lock:
        _remember_processed_locked(event_key)


def _prime_processed_cache() -> None:
    """Fills the index from the database once, on first use, so a restart
    doesn't pay one query per already-answered comment and message to
    rediscover what it knew before.

    Marked primed BEFORE the query, and never retried on failure: priming is
    an optimisation, and a database that cannot answer this will fail the
    individual lookups below just as visibly. Retrying it every cycle would
    turn one bad moment into a permanent extra query per cycle.
    """
    global _processed_cache_primed
    with _processed_cache_lock:
        if _processed_cache_primed:
            return
        _processed_cache_primed = True
    try:
        keys = instagram_contact_repository.get_recent_processed_event_keys(_PROCESSED_CACHE_LIMIT)
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(
            f"Could not preload the Instagram processed-event index ({type(exc).__name__}): {exc} — "
            "it will fill in as events are seen."
        )
        return
    with _processed_cache_lock:
        for key in keys:
            _remember_processed_locked(key)
    step_logger.info(
        f"Instagram processed-event index loaded with {len(keys)} already-handled event(s) — repeat comments "
        "and DMs are recognised from memory instead of a database lookup each time."
    )


def get_contact(ig_user_id: str) -> Optional[InstagramContactRecord]:
    if is_client_database_configured():
        return instagram_contact_repository.get_contact(ig_user_id)
    return _contacts.get(ig_user_id)


def upsert_contact(record: InstagramContactRecord) -> InstagramContactRecord:
    if is_client_database_configured():
        return instagram_contact_repository.upsert_contact(record)
    # Mirrors the "a quota may never travel backwards" guard the database
    # path enforces in instagram_contact_repository.upsert_contact, so the
    # two backends agree about requirement_submission_count.
    existing = _contacts.get(record.ig_user_id)
    if existing is not None and existing.requirement_submission_count > record.requirement_submission_count:
        record = record.model_copy(
            update={"requirement_submission_count": existing.requirement_submission_count}
        )
    _contacts[record.ig_user_id] = record
    return record


def get_all_contacts(limit: int = 100) -> List[InstagramContactRecord]:
    if is_client_database_configured():
        return instagram_contact_repository.get_all_contacts(limit)
    return list(_contacts.values())[-limit:]


def get_contact_count() -> int:
    if is_client_database_configured():
        return instagram_contact_repository.get_contact_count()
    return len(_contacts)


def is_event_processed(event_key: str) -> bool:
    if not is_client_database_configured():
        return event_key in _processed_events
    _prime_processed_cache()
    with _processed_cache_lock:
        if event_key in _processed_cache:
            _processed_cache.move_to_end(event_key)
            return True
    # Not known to be processed — which is NOT the same as "new", so the
    # database still decides. Only the "yes" is worth remembering; a "no"
    # becomes a "yes" moments later via mark_event_processed anyway.
    if instagram_contact_repository.is_event_processed(event_key):
        _remember_processed(event_key)
        return True
    return False


def mark_event_ignored(event_key: str) -> None:
    """Marks an event as "never look at this again" WITHOUT writing a row.

    Used for exactly one thing: an event dropped because its author has used
    up their daily allowance (see Middleware/daily_quota.py). Those need the
    same never-reconsidered treatment a handled event gets — Instagram keeps
    returning the same recent comments and messages on every 8-second cycle,
    so an unmarked drop would be re-examined, and re-queried, forever — but
    they must not cost a database write, because "this costs us nothing"
    is the entire point of dropping them.

    Memory-only is also what makes the 6 AM reset behave the way it should:
    a flood that was ignored yesterday stays ignored today, so the new
    window starts on what arrives in it rather than on a backlog.

    The one thing lost by not persisting it is a restart: after one, a
    dropped event can be seen as new again, and will then be measured
    against that day's allowance like anything else. That is the correct
    failure — it costs at most one allowance, never a duplicate reply to
    something already answered, because a genuinely ANSWERED event was
    marked by mark_event_processed below and is in the database.
    """
    if not is_client_database_configured():
        _processed_events.add(event_key)
        return
    _remember_processed(event_key)


def mark_event_processed(event_key: str) -> None:
    if not is_client_database_configured():
        _processed_events.add(event_key)
        return
    instagram_contact_repository.mark_event_processed(event_key)
    # Recorded in memory only after the write succeeded — a failed write that
    # was cached anyway would make the poller skip an event it never actually
    # recorded, which is exactly the duplicate-reply hole this guard exists
    # to close.
    _remember_processed(event_key)
