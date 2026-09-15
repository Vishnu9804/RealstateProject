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

from Database import settings_repository
from Database.session import is_database_configured
from Middleware import step_logger
from Service.BrokerRequirementService import requirement_matching_service
from Service.ClientPropertyMatchingService import match_candidates, matching_service
from Service.WhatsAppInquiryHandlingService import client_store

# Fixed +5:30 offset, not zoneinfo("Asia/Kolkata") — IST has no DST, so a
# plain fixed-offset timezone is exact and needs no tz-database dependency.
_IST = timezone(timedelta(hours=5, minutes=30))
_RUN_HOUR_IST = 6

# Same "effectively all clients" convention already used by
# Service/AgentManagementService/agent_store.py's own get_all_clients call.
_ALL_CLIENTS_LIMIT = 5000
# The listings an incremental pass considers come from match_candidates —
# the same source and the same ceilings a full recompute uses, so an
# incremental pass can never consider a narrower set than the full pass it
# is standing in for.

# app_settings key recording that the one-time builder-project pass (see
# start_builder_project_introduction_in_background) has completed. Written
# only after a pass with no failures, so a failed or interrupted one simply
# runs again on the next start.
_BUILDER_PROJECT_INTRODUCTION_KEY = "builder_project_matching_v1"
# How long after startup that pass waits before doing anything heavy, so it
# never competes with the WhatsApp connections and the first page loads.
_BUILDER_PROJECT_INTRODUCTION_DELAY_SECONDS = 120


def start_daily_recompute_in_background() -> None:
    thread = threading.Thread(target=_daily_loop, name="daily-match-recompute", daemon=True)
    thread.start()
    step_logger.info(
        f"Daily match recompute scheduled for {_RUN_HOUR_IST:02d}:00 IST every day "
        "(re-scores every existing client, then every broker requirement, against new/edited properties "
        "and builder projects)."
    )
    start_builder_project_introduction_in_background()


def start_builder_project_introduction_in_background() -> None:
    """Builder projects are matched like properties from now on — a new or
    edited one reaches every client and requirement through the same
    incremental catch-up a property does. What that catch-up can NOT reach
    are the builder projects saved before this: each is older than every
    existing watermark, so for an existing client or requirement it would
    only ever appear after a full re-score.

    This closes that gap exactly once: every existing client and every
    already-scored broker requirement is scored against the builder projects
    ONLY (never re-scoring a single property), and the result merged into
    what is stored — without moving any watermark, so properties changed
    since keep being caught up exactly as before. Recorded in app_settings
    once it completes cleanly, so every later start costs one primary-key
    lookup and nothing else.

    Database mode only: without DATABASE_URL nothing survives a restart, so
    there is nothing older than the current process to catch up. On its own
    one-shot daemon thread, so neither startup nor the 6 AM schedule ever
    waits for it."""
    if not is_database_configured():
        return
    threading.Thread(target=_introduce_builder_projects_once, name="builder-project-matching-intro", daemon=True).start()


def _introduce_builder_projects_once() -> None:
    try:
        if settings_repository.get_value(_BUILDER_PROJECT_INTRODUCTION_KEY):
            return
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"[Matching] Could not check the builder-project matching flag (retried next start): {exc!r}")
        return
    time.sleep(_BUILDER_PROJECT_INTRODUCTION_DELAY_SECONDS)
    try:
        client_failures, clients_done = _score_builder_projects_for_existing_clients()
        requirements_done, requirement_rows, requirement_failures = (
            requirement_matching_service.score_builder_projects_for_scored_requirements()
        )
        if client_failures or requirement_failures:
            step_logger.warn(
                f"[Matching] Builder projects were scored for {clients_done} client(s) and {requirements_done} "
                f"requirement(s), but {client_failures + requirement_failures} failed — the pass runs again on the "
                "next start."
            )
            return
        settings_repository.set_value(_BUILDER_PROJECT_INTRODUCTION_KEY, {"done": True})
        step_logger.success(
            f"[Matching] Existing builder projects are now matched — scored for {clients_done} client(s) and "
            f"{requirements_done} broker requirement(s) ({requirement_rows} requirement match(es) written)."
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"[Matching] Builder-project matching pass failed ({type(exc).__name__}): {exc!r} — it runs again on "
            "the next start."
        )


def _score_builder_projects_for_existing_clients() -> tuple:
    """(failures, clients scored) — every registered client scored against
    the builder projects only, merged into their cached matches through the
    same incremental path the daily run uses (rescore_changed_properties).
    Their daily watermark (clients.matches_computed_at) is deliberately left
    alone, for the reason given in start_builder_project_introduction_in_background."""
    candidates = match_candidates.get_builder_projects()
    if not candidates:
        return 0, 0
    clients = client_store.get_all_clients(limit=_ALL_CLIENTS_LIMIT)
    if not clients:
        return 0, 0
    stored_vectors = client_store.get_requirement_embeddings([client.phone for client in clients])
    computed_at = datetime.now(timezone.utc)
    failures = 0
    scored = 0
    for client in clients:
        if not matching_service.has_requirements(client):
            continue
        try:
            matching_service.rescore_changed_properties(
                client, candidates, computed_at, stored_vector=stored_vectors.get(client.phone)
            )
            scored += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            step_logger.error(
                f"[Matching] Builder projects could not be scored for {client.phone} ({type(exc).__name__}): {exc!r}"
            )
    return failures, scored


def _daily_loop() -> None:
    while True:
        time.sleep(_seconds_until_next_run())
        # Back to back on one thread, not two schedulers: the database wakes
        # once for both, and the two CPU-heavy passes never overlap.
        for job in (_recompute_all_clients, _rescore_all_broker_requirements):
            try:
                job()
            except Exception as exc:  # noqa: BLE001
                # One bad cycle (a transient DB/embedding error) must never
                # kill this thread, nor skip the other pass — the next day's
                # run is the retry.
                step_logger.error(
                    f"[Daily Matching] {job.__name__} failed ({type(exc).__name__}): {exc!r}"
                )


def _rescore_all_broker_requirements() -> None:
    """The same catch-up for Broker Requirement Matching — each requirement
    compared only against properties added or edited since it was last
    scored (see requirement_matching_service.rescore_all_requirements)."""
    rescored, up_to_date, written = requirement_matching_service.rescore_all_requirements()
    step_logger.success(
        f"[Daily Matching] Broker requirement rescore finished — {rescored} requirement(s) rescored "
        f"({written} match(es) written), {up_to_date} already up to date."
    )


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
            changed = match_candidates.get_changed_since(since)
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
