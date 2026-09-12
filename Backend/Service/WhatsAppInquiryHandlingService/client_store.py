"""Storage abstraction for client records — the one place the
inquiry-handling pipeline (and, later, the registration/update form and
dashboard endpoints) goes to read/write a client's info + requirements.
Callers never know or care which backend is active underneath:

  - DATABASE_URL unset (the default until it's configured): falls
    back to an in-memory dict, keyed by E.164 phone number.
  - DATABASE_URL set: delegates to Database/client_repository.py
    (Postgres/Neon).

Mirrors Service/WhatsAppDataFetchingService/property_vector_store.py's role
for the property pipeline. This is also, deliberately, the ONLY place
client records are held — nothing else keeps a second copy to keep in sync.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from Database import client_repository
from Database.client_session import is_client_database_configured
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

# In-memory fallback only — untouched whenever the client database is configured.
_clients: Dict[str, ClientRecord] = {}
# Bumped on every in-memory upsert — the fallback's equivalent of
# ClientRow.updated_at. Only ever read by get_clients_version below.
_version_counter = 0


def get_client_by_phone(phone: str) -> Optional[ClientRecord]:
    if is_client_database_configured():
        return client_repository.get_client_by_phone(phone)
    return _clients.get(phone)


def upsert_client(
    record: ClientRecord,
    previous: Optional[ClientRecord] = None,
    defer_recompute: bool = False,
) -> ClientRecord:
    """`previous` is this client's state BEFORE this write, and is only ever
    an optimisation: callers that have already read the record (the
    registration form, which needs it to count updates) pass it so this
    function does not go and read exactly the same row a second time. Leave
    it out and it is read here, exactly as it always was.

    `defer_recompute=True` runs the match recompute on a background thread
    instead of inside this call. It exists for ONE kind of caller: a public
    web form with a person watching a spinner. A recompute embeds the
    requirements and rewrites every cached match row for this client, which
    is far and away the slowest part of a submission and produces nothing
    the browser is waiting for — the page's answer is "saved", and the
    matches are for the dashboard to show later. Every internal caller
    leaves it False and keeps the old, strictly-ordered behaviour, so
    nothing on the dashboard can read a half-computed result.
    """
    if previous is None:
        previous = get_client_by_phone(record.phone)

    if is_client_database_configured():
        saved = client_repository.upsert_client(record)
    else:
        global _version_counter
        _version_counter += 1
        # Same "a quota may never travel backwards" rule the database path
        # enforces in client_repository.upsert_client — see the comment
        # there. Without it the two backends would disagree about the one
        # field whose whole purpose is to be hard to reset.
        if previous is not None and previous.requirement_submission_count > record.requirement_submission_count:
            record = record.model_copy(
                update={"requirement_submission_count": previous.requirement_submission_count}
            )
        _clients[record.phone] = record
        saved = record

    # Client-Property Matching feature: auto-recompute whenever a
    # requirement field actually changed — not on every save (a
    # pending_action toggle or a name/email-only edit re-saves the whole
    # record too, and shouldn't trigger a pointless rescore). Lazy import
    # + broad except so a matching failure can never break the inquiry
    # pipeline that just successfully saved this client's data; this is
    # the only place whatsappInquiryHandling depends on the matching
    # feature at all.
    try:
        from Service.ClientPropertyMatchingService import matching_service

        if matching_service.requirement_fields_changed(previous, saved):
            if defer_recompute:
                _schedule_recompute(saved.phone)
            else:
                matching_service.recompute_for_client(saved.phone)
    except Exception as exc:  # noqa: BLE001
        from Middleware import step_logger

        step_logger.error(f"[Matching] Failed to auto-recompute matches for {saved.phone}: {exc!r}")

    # This number is now, definitively, a client. Telling the public site's
    # known-client cache so costs nothing and closes the only window it
    # has: without this, a brand-new client whose number was looked up
    # minutes earlier (and cached as "not one of ours") would keep being
    # sent verification codes until that negative entry aged out. Lazy
    # import and a swallowed failure for the same reason the matching call
    # above has them — a cache hint may never break a save that succeeded.
    try:
        from Service.WhatsAppInquiryHandlingService import known_client_cache

        known_client_cache.remember(saved.phone, True)
    except Exception:  # noqa: BLE001
        pass

    return saved


# --- deferred recompute (see upsert_client's defer_recompute) -------------
#
# One worker per client at a time, never one per save. Two saves for the
# same person arriving close together — which is exactly what a form being
# re-submitted looks like — would otherwise run two full recomputes
# concurrently against the same rows: twice the embedding work, twice the
# match-table rewrite, and a race over which one's result lands last. This
# collapses them: the second save sets a "run again when you're done" flag,
# and the worker loops once more afterwards, reading the client fresh, so
# the final cached matches always reflect the final saved requirements.
_recompute_lock = threading.Lock()
_recompute_running: Set[str] = set()
_recompute_again: Set[str] = set()


def _schedule_recompute(phone: str) -> None:
    with _recompute_lock:
        if phone in _recompute_running:
            _recompute_again.add(phone)
            return
        _recompute_running.add(phone)
    threading.Thread(
        target=_recompute_worker, args=(phone,), name="client-match-recompute", daemon=True
    ).start()


def _recompute_worker(phone: str) -> None:
    from Middleware import step_logger

    while True:
        try:
            from Service.ClientPropertyMatchingService import matching_service

            matching_service.recompute_for_client(phone)
        except Exception as exc:  # noqa: BLE001
            # Never re-raised, and never left to strand the flag below: a
            # failed recompute must not make this client's future saves stop
            # scheduling one. The data itself is already safely stored — the
            # nightly pass (scheduled_recompute_service.py) picks this client
            # up regardless.
            step_logger.error(f"[Matching] Background recompute failed for {phone}: {exc!r}")
        with _recompute_lock:
            if phone in _recompute_again:
                _recompute_again.discard(phone)
                continue
            _recompute_running.discard(phone)
            return


def delete_client(phone: str) -> bool:
    """Removes one client entirely. Returns False when there was nothing to
    remove. Callers must clear whatever references this client first (see
    Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
    delete_client) — completed VISITS are the deliberate exception and are
    always kept, so a future enquiry from the same number still sees the
    properties it has already been shown."""
    # Whatever happens below, this number is no longer a client — and the
    # public site must stop treating it as one immediately, or a deleted
    # client's number would keep skipping verification for hours.
    try:
        from Service.WhatsAppInquiryHandlingService import known_client_cache

        known_client_cache.remember(phone, False)
    except Exception:  # noqa: BLE001
        pass

    if is_client_database_configured():
        return client_repository.delete_client(phone)
    global _version_counter
    if phone not in _clients:
        return False
    del _clients[phone]
    _version_counter += 1
    return True


def client_exists(phone: str) -> bool:
    """Whether we hold a client record for this E.164 number, and nothing
    about what is in it.

    Used to be `get_client_by_phone(...) is not None`, which read the whole
    row to answer a yes/no. That was harmless while every caller was an
    internal one; it stopped being harmless when the public site's number
    confirmation started asking the same question (see
    known_client_cache.py), because that is a route an anonymous stranger
    can call. The database path now selects the primary key alone — an
    index probe and a few bytes on the wire instead of a full client
    record.

    Callers on the public path must go through known_client_cache.py rather
    than here: this function has no cache and no ceiling in front of it, so
    it costs a query every single time it is called."""
    if is_client_database_configured():
        return client_repository.client_exists(phone)
    return phone in _clients


def get_all_clients(limit: int = 100) -> List[ClientRecord]:
    if is_client_database_configured():
        return client_repository.get_all_clients(limit)
    return list(_clients.values())[-limit:]


def get_client_count() -> int:
    if is_client_database_configured():
        return client_repository.get_client_count()
    return len(_clients)


def get_clients_version() -> str:
    """A single comparable string the Inquiries page holds onto and diffs
    against, so it only re-fetches the full client list when something
    actually changed — see property_vector_store.get_properties_version for
    the same pattern applied to properties."""
    if is_client_database_configured():
        count, latest = client_repository.get_clients_version()
        return f"{count}:{latest.isoformat() if latest else '0'}"
    return f"{len(_clients)}:{_version_counter}"


# In-memory fallback only — the stand-in for ClientRow.matches_computed_at.
_matches_computed_at: Dict[str, datetime] = {}


def set_matches_computed_at(watermarks: Dict[str, datetime]) -> None:
    """Records when each of these clients was last scored against the
    property list — see ClientRow.matches_computed_at. Takes a map so the
    nightly run stamps every client it processed in one write."""
    if is_client_database_configured():
        client_repository.set_matches_computed_at(watermarks)
        return
    _matches_computed_at.update(watermarks)


def get_matches_computed_at(phones: List[str]) -> Dict[str, Optional[datetime]]:
    """Each client's last-scored watermark. A phone missing from the result
    (or mapped to None) has never been scored, and needs a full pass."""
    if is_client_database_configured():
        return client_repository.get_matches_computed_at(phones)
    return {phone: _matches_computed_at.get(phone) for phone in phones}


def get_requirement_embeddings(phones: List[str]) -> Dict[str, List[float]]:
    """The requirement vectors already stored for these clients, so an
    incremental rescore can reuse one instead of deriving it again.

    Empty in the in-memory fallback: that backend never persisted these
    vectors in the first place, so there is nothing to reuse and the caller
    correctly falls back to computing one."""
    if is_client_database_configured():
        return client_repository.get_requirement_embeddings(phones)
    return {}


def assign_agent(phone: str, agent_id: Optional[str]) -> Optional[ClientRecord]:
    """AgentManagement feature: sets (or clears, with agent_id=None) which
    agent is handling this client's site visit. Goes through upsert_client
    like every other client write — assigned_agent_id isn't one of the
    requirement fields upsert_client checks, so this never triggers a
    pointless Client-Property Matching recompute."""
    record = get_client_by_phone(phone)
    if record is None:
        return None
    return upsert_client(record.model_copy(update={"assigned_agent_id": agent_id}))


def mark_handoff_sent(phone: str) -> Optional[ClientRecord]:
    """AgentManagement feature: records that the "Send both on WhatsApp"
    hand-off action fired for this client — an audit trail, not a trigger
    for anything else."""
    record = get_client_by_phone(phone)
    if record is None:
        return None
    return upsert_client(record.model_copy(update={"handoff_sent_at": datetime.now(timezone.utc)}))
