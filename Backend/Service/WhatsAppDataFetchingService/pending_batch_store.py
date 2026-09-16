"""The last line of defence for the rule this pipeline is built around: a
captured message is never, ever dropped.

Everything upstream of here is about a batch SUCCEEDING — the streamed
request, the repetition guard, the in-call retries, the concurrency gate in
Agent/WhatsAppDataFetchingAgent/glm_gate.py. This module is about what
happens when it still doesn't, and about the two places a message used to
exist ONLY in this process's memory:

  1. Sitting in the buffering stage, waiting for its batch to fill up (up to
     10 messages, up to a full hour). Killing the server threw those away.
  2. In flight between the buffer and the pipeline, or inside a batch whose
     GLM call ran out of attempts. The structuring stage's own docstring said
     so outright — "it is simply lost for now, there is nowhere durable to
     retry it from".

Both are covered here now, and they are covered by ONE record with no gap
between them: the buffer hands custody of a batch to this module BEFORE it
drops it from its own snapshot, so at every instant in a message's life it
exists in at least one file on disk.

The two kinds of file
---------------------
  PendingBatches/buffers/<name>.json — what a buffering stage is holding
      right now, rewritten on every message and removed the moment the batch
      is handed over. Restored into the same buffer on the next start, with
      the remainder of its original batch window (see message_buffer_service).

  PendingBatches/<batch id>.json — one batch that has left the buffer, in one
      of two states:
        "in_progress" — a thread is structuring it at this very moment
        "waiting"     — it failed, and is scheduled to be retried

A single background worker re-submits due records through the exact same
pipeline entry point the live batch used, and a record is deleted only once
its batch has been handled successfully.

The state is what makes crash recovery exact rather than guessed at. Within a
running process, this module knows which records its own threads are working
on. So an "in_progress" record that nobody is working on can only mean one
thing: the process that owned it died. On the next start there are no working
threads at all, so every "in_progress" record left on disk is orphaned by
definition and is retried immediately — no timeout to tune, no window in
which a batch is neither running nor recoverable.

Four properties make this safe rather than just hopeful:

  - It survives a restart, a crash and a deploy. Every message is on disk
    from the moment it is buffered until the moment its batch is processed.
  - Retrying cannot duplicate anything. BOTH pipelines drop already-processed
    text by content fingerprint before their LLM stage
    (property_pipeline_service._drop_duplicate_messages and the requirement
    pipeline's own), so re-running a batch that partly succeeded re-does only
    the part that didn't. This is why a whole-batch retry is the right unit
    and no partial bookkeeping is needed here.
  - It never gives up. The interval escalates to a ceiling and then stays
    there. A batch stuck behind an expired API key or an exhausted daily
    allowance keeps waiting, and goes through by itself the moment the cause
    is fixed — which is the entire point of the rule.
  - It never makes things worse. The worker is one thread, retries one batch
    at a time, and its GLM call goes through the same concurrency gate as
    live traffic, so a backlog draining can't crowd out new messages or
    trigger the rate limit that caused the backlog.

Why files rather than a database table: this has to work when the database
itself is what's unavailable, and it has to be readable by a human during an
incident. Small JSON files are both. They are runtime state, not source —
PendingBatches/ is git-ignored alongside the other generated directories.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage

# Backend/Service/WhatsAppDataFetchingService/this_file.py
#   parents[2] = .../Backend    <- uvicorn's --reload watch root
#   parents[3] = .../<project root>
# Outside the watch root on purpose, exactly like KnowledgeBase/: writing a
# file under Backend/ while uvicorn --reload is running would restart the
# server on every buffered message.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
PENDING_DIR = _PROJECT_ROOT / "PendingBatches"
# A subdirectory, so the batch-record scan below (a NON-recursive glob of
# PENDING_DIR) can never mistake a live buffer snapshot for a batch to retry.
BUFFER_DIR = PENDING_DIR / "buffers"

PROPERTY_PIPELINE = "property"
REQUIREMENT_PIPELINE = "requirement"

_IN_PROGRESS = "in_progress"
_WAITING = "waiting"

# How long after each failure the batch waits before the next attempt. The
# first few are short because the common causes (a rate-limit burst, a brief
# Z.ai wobble) clear in minutes; the tail is long because the rest (an
# exhausted daily allowance, a bad key, an outage) clear in hours, and
# hammering them helps nobody. The last value repeats forever — a batch is
# never abandoned, only slowed down.
_RETRY_DELAYS_SECONDS = (60, 180, 600, 1_800, 3_600, 7_200)

# How often the worker looks for due records. Cheap: a directory listing of a
# directory that is empty in the normal case.
_TICK_SECONDS = 20.0

# After this many failed attempts the batch is still retried on the same
# schedule, but every attempt says so loudly — at ~6 attempts the batch has
# been stuck for over two hours and something needs a human.
_SHOUT_AFTER_ATTEMPTS = 6

_write_lock = threading.Lock()
_worker: Optional[threading.Thread] = None

# Batch ids this process is structuring RIGHT NOW. The worker skips these;
# anything on disk marked "in_progress" that is NOT in here was orphaned by a
# crash and is retried immediately. Empty at start-up, which is exactly why
# every record a dead process left behind is picked up on the next run.
_active_lock = threading.Lock()
_active: Set[str] = set()

# Which batch the CURRENT thread is handling, set by working_on(). It is what
# lets the pipeline's own claim() find the record the buffer (or the retry
# worker) already created for this batch, instead of writing a second one.
_current = threading.local()


# ------------------------------------------------------- buffer snapshots


def save_buffer(name: str, messages: List[WhatsAppChatMessage], window_started_at: Optional[float]) -> None:
    """Records everything a buffering stage is holding, so a process that
    dies before the batch fills up loses nothing. Called on every buffered
    message — the file is at most one batch (10 messages), so this is a small
    write, not a growing log.

    `window_started_at` is wall-clock epoch seconds, not monotonic: it has to
    survive the restart it exists for, so the restored buffer can serve out
    the REMAINDER of its original window rather than starting a fresh one
    (see message_buffer_service.restore_from_disk).

    Never raises. A snapshot that cannot be written is a warning, never a
    reason to fail the message that triggered it."""
    try:
        if not messages:
            clear_buffer(name)
            return
        _write(
            BUFFER_DIR / f"{name}.json",
            {
                "name": name,
                "saved_at": _now_iso(),
                "window_started_at": window_started_at,
                "messages": [message.model_dump(mode="json") for message in messages],
            },
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Could not save the {name} buffer to disk ({exc!r}) — the message itself is unaffected.")


def load_buffer(name: str) -> Tuple[List[WhatsAppChatMessage], Optional[float]]:
    """What that buffering stage was holding when this process last stopped,
    and when its batch window had started. ([], None) when there is nothing
    to restore, which is the normal case after a clean run."""
    payload = _read(BUFFER_DIR / f"{name}.json")
    if not payload:
        return [], None
    messages = _rebuild_messages(payload)
    window_started_at = payload.get("window_started_at")
    return messages, float(window_started_at) if isinstance(window_started_at, (int, float)) else None


def message_ids_in_records() -> Set[str]:
    """Every message id currently held in a batch record.

    Read once at start-up so a restored buffer can drop anything a record
    already owns. There is exactly one instant where a message can be in both
    files: _flush_locked claims the batch BEFORE rewriting the buffer
    snapshot without it (deliberately — the other order would lose the batch
    outright if the process died in between). Dying inside those few
    microseconds leaves the messages in both places, and without this filter
    the retry worker and the restored buffer would both structure them, at the
    same moment, before either could store a fingerprint for the other to
    notice. The record owns them; the buffer lets them go."""
    owned: Set[str] = set()
    try:
        paths = sorted(PENDING_DIR.glob("*.json")) if PENDING_DIR.exists() else []
    except Exception:  # noqa: BLE001
        return owned
    for path in paths:
        payload = _read(path)
        for raw in (payload or {}).get("messages") or []:
            message_id = raw.get("message_id") if isinstance(raw, dict) else None
            if isinstance(message_id, str):
                owned.add(message_id)
    return owned


def clear_buffer(name: str) -> None:
    """Drops the snapshot once its messages are safely somewhere else. Only
    ever called AFTER the batch has been claimed as a durable record (see
    claim), so there is no instant where the messages exist in neither."""
    _delete(BUFFER_DIR / f"{name}.json")


# ---------------------------------------------------------- batch records


def claim(pipeline: str, messages: List[WhatsAppChatMessage]) -> Optional[str]:
    """Takes durable custody of a batch that is about to be structured, and
    returns its record id. Call it BEFORE any work starts; pair it with
    exactly one release() or defer().

    Returns the EXISTING id when this thread is already handling a record
    (the buffer claimed it at hand-off, or the retry worker is re-running it
    — see working_on), so a batch never ends up with two records. Returns
    None only if the record could not be written at all, which is logged and
    otherwise ignored: a disk problem must not stop a batch being processed.
    """
    existing = getattr(_current, "batch_id", None)
    if existing is not None:
        _mark(existing, _IN_PROGRESS)
        return existing
    return _create(pipeline, messages, reason="", state=_IN_PROGRESS)


def release(batch_id: Optional[str]) -> None:
    """The batch is done — its record is no longer needed. Anything that was
    stored is stored, and anything the model decided was not a listing has
    been decided."""
    if batch_id is None:
        return
    with _active_lock:
        _active.discard(batch_id)
    _delete(PENDING_DIR / f"{batch_id}.json")


def defer(
    batch_id: Optional[str], pipeline: str, messages: List[WhatsAppChatMessage], reason: str
) -> Optional[str]:
    """The batch could not be completed — schedule it to be retried. Keeps
    the messages on disk, bumps the attempt count and pushes the next attempt
    out along _RETRY_DELAYS_SECONDS.

    Falls back to writing a fresh record when there is no claim to update
    (claim() failed, or the record was removed underneath us), so a failure is
    never silently un-recorded."""
    if batch_id is not None:
        with _active_lock:
            _active.discard(batch_id)
        payload = _read(PENDING_DIR / f"{batch_id}.json")
        if payload is not None:
            attempts = int(payload.get("attempts") or 0) + 1
            _schedule(payload, attempts, reason)
            if attempts >= _SHOUT_AFTER_ATTEMPTS:
                step_logger.error(
                    f"Held {pipeline} batch {batch_id} has now failed {attempts} times over "
                    f"{_age_description(payload)}. It is STILL queued and will keep retrying, but the "
                    "cause needs looking at (Z.ai key/credit/allowance, or network)."
                )
            else:
                step_logger.warn(
                    f"Held {len(payload.get('messages') or messages)} {pipeline} message(s) for retry "
                    f"instead of dropping them (batch {batch_id}, attempt {attempts}): {reason}. "
                    f"Next try in about {_RETRY_DELAYS_SECONDS[min(attempts, len(_RETRY_DELAYS_SECONDS)) - 1] // 60} "
                    "minute(s); this batch is on disk, so it also survives a restart."
                )
            return batch_id
    return enqueue(pipeline, messages, reason)


def enqueue(pipeline: str, messages: List[WhatsAppChatMessage], reason: str) -> Optional[str]:
    """Writes a NEW record for messages that must be retried but have no
    claim of their own — the subset of a batch the model never answered for,
    and demand messages the re-route could not hand over. Always a new
    record: these messages are a fragment of some other batch, with their own
    lifecycle from here on.

    Never raises. It is called from failure handlers, and an exception
    escaping one would replace a recoverable problem with an unrecoverable
    one."""
    if not messages:
        return None
    batch_id = _create(pipeline, messages, reason=reason, state=_WAITING)
    if batch_id is not None:
        step_logger.warn(
            f"Held {len(messages)} {pipeline} message(s) for retry instead of dropping them "
            f"(batch {batch_id}): {reason}. First retry in "
            f"{_RETRY_DELAYS_SECONDS[0] // 60} minute(s); this batch is now on disk, so it also "
            "survives a restart."
        )
    return batch_id


@contextmanager
def working_on(batch_id: Optional[str]) -> Iterator[None]:
    """Marks this thread as the one handling `batch_id` for the duration of
    the block, so the pipeline's own claim() finds that record rather than
    creating a second one for the same messages. Used by the buffering stage
    at hand-off and by the retry worker."""
    previous = getattr(_current, "batch_id", None)
    _current.batch_id = batch_id
    if batch_id is not None:
        with _active_lock:
            _active.add(batch_id)
    try:
        yield
    finally:
        _current.batch_id = previous


def pending_count() -> int:
    """How many batches are waiting to be retried right now — records this
    process is actively structuring are normal work, not a backlog, and are
    excluded. Surfaced on the WhatsApp status endpoint so a real backlog is
    visible rather than silent."""
    try:
        total = sum(1 for _ in PENDING_DIR.glob("*.json"))
    except Exception:  # noqa: BLE001
        return 0
    with _active_lock:
        return max(total - len(_active), 0)


# ------------------------------------------------------------------ worker


def start_retry_worker_in_background() -> None:
    """Starts the single retry worker. Idempotent, and safe to call before
    anything has ever failed — with an empty directory each tick is one
    listing and nothing else."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return

    waiting = pending_count()
    if waiting:
        step_logger.warn(
            f"{waiting} batch(es) from a previous run are still waiting to be structured — "
            "retrying them in the background. No message in them has been lost."
        )

    _worker = threading.Thread(target=_run_worker, name="pending-batch-retry", daemon=True)
    _worker.start()


def _run_worker() -> None:
    while True:
        try:
            _retry_due_batches()
        except Exception as exc:  # noqa: BLE001
            # The worker must outlive any single bad tick: if it dies, every
            # queued batch stops being retried, which is the failure this
            # whole module exists to prevent.
            step_logger.error(f"The pending-batch retry worker hit an unexpected error: {exc!r}")
        time.sleep(_TICK_SECONDS)


def _retry_due_batches() -> None:
    now = _now_timestamp()
    for path in sorted(PENDING_DIR.glob("*.json")) if PENDING_DIR.exists() else []:
        payload = _read(path)
        if payload is None:
            continue
        if not _is_due(payload, now):
            continue
        try:
            _retry_batch(path, payload)
        except Exception as exc:  # noqa: BLE001
            # Per record, not per tick: one unreadable or unroutable file must
            # not stop every OTHER held batch from being retried behind it,
            # and must not leave this one instantly due again in a hot loop.
            step_logger.error(f"Could not retry pending batch {path.name} ({exc!r}) — it stays queued.")
            _schedule(payload, int(payload.get("attempts") or 0) + 1, f"the retry itself failed: {exc!r}")


def _is_due(payload: Dict[str, Any], now: float) -> bool:
    """A "waiting" record is due once its timer expires. An "in_progress" one
    is due only when no thread in THIS process is handling it — which, after a
    restart, is true of every in_progress record on disk, because the process
    that was handling them is gone. That is the whole crash-recovery rule."""
    batch_id = str(payload.get("batch_id") or "")
    if payload.get("state") == _IN_PROGRESS:
        with _active_lock:
            return batch_id not in _active
    return float(payload.get("next_attempt_at") or 0) <= now


def _retry_batch(path: Path, payload: Dict[str, Any]) -> None:
    batch_id = str(payload.get("batch_id") or path.stem)
    pipeline = str(payload.get("pipeline") or "")
    attempts = int(payload.get("attempts") or 0)

    messages = _rebuild_messages(payload)
    if not messages:
        step_logger.error(
            f"Pending batch {batch_id} could not be read back into messages — leaving the file in "
            f"{PENDING_DIR} untouched for a human rather than deleting anything."
        )
        _schedule(payload, attempts + 1, "the saved messages could not be read back")
        return

    handler = _handler_for(pipeline)
    if handler is None:
        step_logger.error(f"Pending batch {batch_id} names an unknown pipeline {pipeline!r} — leaving it on disk.")
        _schedule(payload, attempts + 1, f"unknown pipeline {pipeline!r}")
        return

    orphaned = payload.get("state") == _IN_PROGRESS
    step_logger.step(
        f"Retrying held {pipeline} batch {batch_id} ({len(messages)} message(s), attempt {attempts + 1})"
        + (" — it was still marked in progress, so the run that owned it did not finish" if orphaned else "")
        + ". Anything in it that was already stored is skipped by the pipeline's own duplicate check."
    )
    _mark(batch_id, _IN_PROGRESS)

    # The handler claims this same record (see claim/working_on) and finishes
    # it with exactly one release() or defer(), so nothing more is needed here
    # on the happy path.
    with working_on(batch_id):
        try:
            handler(messages)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Held {pipeline} batch {batch_id} raised on retry — keeping it queued: {exc!r}")
            defer(batch_id, pipeline, messages, f"the retry raised: {exc!r}")
            return

    remaining = _read(path)
    if remaining is None:
        step_logger.success(
            f"Held {pipeline} batch {batch_id} went through on attempt {attempts + 1} — its "
            f"{len(messages)} message(s) are processed. Nothing was lost."
        )
        return
    if remaining.get("state") == _IN_PROGRESS:
        # A handler that neither released nor deferred. Shouldn't happen, but
        # a record stuck in_progress with nobody working on it would otherwise
        # be retried on every single tick.
        defer(batch_id, pipeline, messages, "the retry finished without recording a result")


def _handler_for(pipeline: str) -> Optional[Callable[[List[WhatsAppChatMessage]], None]]:
    """The same entry point the live batch used, imported lazily: both
    pipeline services import THIS module at module level to record their
    batches, so importing them at the top here would be circular."""
    if pipeline == PROPERTY_PIPELINE:
        from Service.WhatsAppDataFetchingService import property_pipeline_service

        return property_pipeline_service.handle_batch_ready
    if pipeline == REQUIREMENT_PIPELINE:
        from Service.BrokerRequirementService import requirement_pipeline_service

        return requirement_pipeline_service.handle_batch_ready
    return None


def _rebuild_messages(payload: Dict[str, Any]) -> List[WhatsAppChatMessage]:
    """Saved message dicts back into models. One unreadable message is
    skipped rather than costing the whole batch — the other nine are still
    real listings."""
    rebuilt: List[WhatsAppChatMessage] = []
    for raw in payload.get("messages") or []:
        try:
            rebuilt.append(WhatsAppChatMessage(**raw))
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Skipping one unreadable saved message in {payload.get('batch_id')!r}: {exc!r}")
    return rebuilt


# ------------------------------------------------------------------- files


def _create(pipeline: str, messages: List[WhatsAppChatMessage], reason: str, state: str) -> Optional[str]:
    if not messages:
        return None
    try:
        batch_id = f"{pipeline}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        _write(
            PENDING_DIR / f"{batch_id}.json",
            {
                "batch_id": batch_id,
                "pipeline": pipeline,
                "state": state,
                "reason": reason,
                "attempts": 0,
                "first_seen_at": _now_iso(),
                "updated_at": _now_iso(),
                "next_attempt_at": _now_timestamp() + (0 if state == _IN_PROGRESS else _RETRY_DELAYS_SECONDS[0]),
                "messages": [message.model_dump(mode="json") for message in messages],
            },
        )
        if state == _IN_PROGRESS:
            with _active_lock:
                _active.add(batch_id)
        return batch_id
    except Exception as exc:  # noqa: BLE001
        # The only remaining trace is this line — deliberately an error, and
        # deliberately carrying the message ids, so the messages can still be
        # found by hand if the disk itself is the problem.
        step_logger.error(
            f"COULD NOT SAVE a {pipeline} batch to disk ({exc!r}). It is still being processed in memory, "
            f"but a crash would now lose it. Reason given: {reason or 'none'}. "
            f"Message ids: {[message.message_id for message in messages]}"
        )
        return None


def _mark(batch_id: str, state: str) -> None:
    path = PENDING_DIR / f"{batch_id}.json"
    payload = _read(path)
    if payload is None or payload.get("state") == state:
        return
    payload["state"] = state
    payload["updated_at"] = _now_iso()
    try:
        _write(path, payload)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not update pending batch file {path.name}: {exc!r}")


def _schedule(payload: Dict[str, Any], attempts: int, reason: str) -> None:
    """Moves a record to "waiting" with the next backoff applied. The delay
    list's last value repeats for every attempt past its end, so a batch is
    slowed down but never abandoned."""
    batch_id = str(payload.get("batch_id") or "")
    if not batch_id:
        return
    delay = _RETRY_DELAYS_SECONDS[min(attempts, len(_RETRY_DELAYS_SECONDS)) - 1]
    payload["state"] = _WAITING
    payload["attempts"] = attempts
    payload["reason"] = reason or payload.get("reason") or ""
    payload["updated_at"] = _now_iso()
    payload["next_attempt_at"] = _now_timestamp() + delay
    try:
        _write(PENDING_DIR / f"{batch_id}.json", payload)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not reschedule pending batch {batch_id}: {exc!r}")


def _write(path: Path, payload: Dict[str, Any]) -> None:
    """Write-then-rename, the same way the knowledge base is written: a crash
    mid-write must never leave a half-file that reads back as a batch with no
    messages in it."""
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not read {path.name} ({exc!r}) — leaving it in place.")
        return None


def _delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        # Harmless in itself, but worth knowing: the batch succeeded, so the
        # only cost of the file surviving is one redundant retry later, which
        # the duplicate check will turn into a no-op.
        step_logger.warn(f"Could not remove {path.name}: {exc!r}")


def _age_description(payload: Dict[str, Any]) -> str:
    raw = payload.get("first_seen_at")
    if not isinstance(raw, str):
        return "an unknown period"
    try:
        first = datetime.fromisoformat(raw)
    except ValueError:
        return "an unknown period"
    minutes = max((datetime.now(timezone.utc) - first).total_seconds() / 60.0, 0.0)
    if minutes < 90:
        return f"{minutes:.0f} minute(s)"
    return f"{minutes / 60.0:.1f} hour(s)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()
