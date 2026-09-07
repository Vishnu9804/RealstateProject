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
from Service.WhatsAppInquiryHandlingService import client_store

# Fixed +5:30 offset, not zoneinfo("Asia/Kolkata") — IST has no DST, so a
# plain fixed-offset timezone is exact and needs no tz-database dependency.
_IST = timezone(timedelta(hours=5, minutes=30))
_RUN_HOUR_IST = 6

# Same "effectively all clients" convention already used by
# Service/AgentManagementService/agent_store.py's own get_all_clients call.
_ALL_CLIENTS_LIMIT = 5000


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
    clients = client_store.get_all_clients(limit=_ALL_CLIENTS_LIMIT)
    step_logger.info(f"[Daily Matching] Scheduled recompute starting for {len(clients)} client(s)...")
    succeeded = 0
    for client in clients:
        try:
            matching_service.recompute_for_client(client.phone)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            # Isolated per client, same reasoning as every other per-item
            # loop in this codebase (e.g. instagram_polling_service._guarded)
            # — one client's failure must never skip the rest.
            step_logger.error(f"[Daily Matching] Recompute failed for {client.phone} ({type(exc).__name__}): {exc!r}")
    step_logger.success(f"[Daily Matching] Scheduled recompute finished — {succeeded}/{len(clients)} client(s) updated.")
