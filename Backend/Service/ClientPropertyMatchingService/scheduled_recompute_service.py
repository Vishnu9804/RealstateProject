"""Daily 6 AM IST re-run of the Client-Property Matching pipeline for every
already-registered client.

Why this exists: matching_service.recompute_for_client(phone) is normally
only triggered when a CLIENT's requirements change (see
Service/WhatsAppInquiryHandlingService/client_store.py's upsert_client) or by
the dashboard's manual Refresh action. Neither of those ever fires when a NEW
PROPERTY is added instead — so a property added the day after a client
registered would otherwise never get scored against that client until the
client happened to edit their requirements again. This background loop closes
that gap by re-running the same, unchanged pipeline for every existing client
once a day, so newly added properties get picked up automatically.

A brand-new client's first-ever match run is untouched by this file: that
still happens exactly as before, synchronously inside upsert_client the
moment their requirements are first saved.

Runs on its own daemon thread with a plain time.sleep loop, mirroring
Service/InstagramInquiryHandlingService/instagram_polling_service.py's
start_background_polling pattern — not asyncio, so a slow recompute cycle
(embedding + scoring calls for every client) can never stall the FastAPI
event loop or the WhatsApp/Instagram background clients.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from Middleware import step_logger
from Service.ClientPropertyMatchingService import matching_service
from Service.WhatsAppDataFetchingService import property_vector_store
from Service.WhatsAppInquiryHandlingService import client_store

# Fixed +5:30 offset, not zoneinfo("Asia/Kolkata") — IST has no DST, so a
# plain fixed-offset timezone is exact and needs no tz-database dependency.
_IST = timezone(timedelta(hours=5, minutes=30))
_RUN_HOUR_IST = 6

# Same "effectively all clients" convention already used by
# Service/AgentManagementService/agent_store.py's own get_all_clients call.
_ALL_CLIENTS_LIMIT = 5000
# Matches matching_service._MAX_PROPERTIES_SCORED — the same ceiling a full
# recompute uses, so an incremental pass can never consider a narrower set
# of properties than the full pass it is standing in for.
_ALL_PROPERTIES_LIMIT = 5000


def start_daily_recompute_in_background() -> None:
    thread = threading.Thread(target=_daily_loop, name="daily-match-recompute", daemon=True)
    thread.start()
    step_logger.info(
        f"Daily match recompute scheduled for {_RUN_HOUR_IST:02d}:00 IST every day "
        "(re-scores every existing client against the current property list)."
    )


def _daily_loop() -> None:
    while True:
        time.sleep(_seconds_until_next_run())
        try:
            _recompute_all_clients()
        except Exception as exc:  # noqa: BLE001
            # One bad cycle (a transient DB/embedding error) must never kill
            # this thread — the next day's run is the retry.
            step_logger.error(f"[Daily Matching] Scheduled recompute cycle failed ({type(exc).__name__}): {exc!r}")


def _seconds_until_next_run() -> float:
    now = datetime.now(_IST)
    next_run = now.replace(hour=_RUN_HOUR_IST, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


def _recompute_all_clients() -> None:
    """One pass over the property list, shared by every client, and each
    client scored only against what is actually new to THEM.

    Two things used to make this the most expensive thing the application
    did. It called recompute_for_client per client, and each of those calls
    independently re-read the entire properties table — photos, embeddings
    and all — twice: once to score with, once to build a result nobody here
    looks at. With twenty clients that was forty full-table transfers in one
    run. And every one of those passes re-scored every property against
    every client from scratch, including the thousands of (client, property)
    pairs that had already been compared the day before, and the day before
    that.

    Now the property list is the in-memory snapshot (no query at all), and
    each client is compared only against properties added or edited since
    that client was last scored. A night on which nothing was added does no
    work and writes nothing.
    """
    clients = client_store.get_all_clients(limit=_ALL_CLIENTS_LIMIT)
    if not clients:
        step_logger.info("[Daily Matching] No clients registered — nothing to rescore.")
        return

    phones = [client.phone for client in clients]
    # Two bulk reads for the whole run, instead of per-client lookups: where
    # each client got to last time, and the requirement vector each already
    # has stored (unchanged since their last full recompute, by definition —
    # a requirements edit triggers one immediately).
    watermarks = client_store.get_matches_computed_at(phones)
    stored_vectors = client_store.get_requirement_embeddings(phones)
    run_started_at = datetime.now(timezone.utc)

    step_logger.info(f"[Daily Matching] Scheduled rescore starting for {len(clients)} client(s)...")
    succeeded = 0
    skipped = 0
    scored_total = 0
    stamped: dict = {}
    for client in clients:
        try:
            since = watermarks.get(client.phone)
            changed = property_vector_store.get_properties_changed_since(since, limit=_ALL_PROPERTIES_LIMIT)
            if not changed:
                # Nothing has arrived or been edited since this client was
                # last scored — their cached matches are already current, so
                # this costs no scoring and no write at all.
                skipped += 1
                stamped[client.phone] = run_started_at
                continue
            scored_total += matching_service.rescore_changed_properties(
                client,
                changed,
                run_started_at,
                stored_vector=stored_vectors.get(client.phone),
            )
            stamped[client.phone] = run_started_at
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            # Isolated per client, same reasoning as every other per-item
            # loop in this codebase (e.g. instagram_polling_service._guarded)
            # — one client's failure must never skip the rest. Deliberately
            # NOT stamped: a client whose rescore failed must be picked up
            # again by the next run, from the same point, rather than having
            # the failure silently marked as done.
            step_logger.error(f"[Daily Matching] Rescore failed for {client.phone} ({type(exc).__name__}): {exc!r}")

    # One write for the whole run's watermarks, stamped with the time the
    # run STARTED — not the time it finished. A property added while the run
    # was in progress may or may not have been seen by a given client, and
    # an end-time watermark would declare it handled for everyone either
    # way; a start-time watermark makes the next run re-examine it, which is
    # harmless (it simply re-scores to the same answer).
    client_store.set_matches_computed_at(stamped)
    step_logger.success(
        f"[Daily Matching] Scheduled rescore finished — {succeeded} client(s) rescored "
        f"({scored_total} match(es) written), {skipped} already up to date."
    )
