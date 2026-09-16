"""Process-wide admission control for EVERY request this backend sends to
Z.ai (GLM), shared by both structuring stages.

Why this exists — the 429s were self-inflicted
----------------------------------------------
Both pipelines flush their batches onto their own threads (see
Service/WhatsAppDataFetchingService/message_buffer_service.py), and the
property stage can fire a second corrective re-ask of its own. Nothing
anywhere coordinated them, so a busy minute put two, three, sometimes four
GLM requests in flight at the same instant. Z.ai answers the extra ones with
HTTP 429 — not because the account is over its daily allowance, but because
it is over its CONCURRENCY allowance. Observed exactly that way in
production:

    15:14:16  batch of 10  -> GLM
    15:14:20  batch of 10  -> GLM             (still in flight: #1)
    15:14:21  429 attempt 1/4
    15:15:01  requirement batch of 2 -> GLM   (still in flight: #1 and #2)
    15:15:03  429 attempt 1/4
    15:15:08  429 attempt 4/4 — batch of 10 abandoned, 0 properties stored

The retries made it worse rather than better: three threads each re-sent
their request into the same saturated concurrency slot, on independent
timers, so every retry was itself another chance to be rejected — and the
batch that finally ran out of attempts took ten real broker messages with
it.

So requests are serialised here instead. Two mechanisms, both global:

  1. A semaphore (_MAX_CONCURRENT_REQUESTS, one by default). At most this
     many GLM requests exist at once no matter how many pipeline threads
     want one. This alone removes the cause of the 429s above.
  2. A shared cool-down clock. When ANY caller is rate-limited, every other
     caller parks until the clock expires — honouring Z.ai's own
     `Retry-After` when it sends one. Without this, thread B would walk
     straight into the limit thread A just discovered.

What this deliberately does NOT do
----------------------------------
It does not slow down anything a human waits on. The only callers are the
two background structuring stages; WhatsApp capture, the inquiry pipeline
(which is Gemini, not GLM) and every HTTP endpoint run on other threads and
never touch this module. Serialising is free here in throughput terms too:
a batch is at most 10 messages and arrives at most once a minute, while one
GLM call takes ~45s — so the queue this creates is almost always empty, and
when it isn't, waiting 45s for a slot is strictly faster than being rejected
and then waiting out a retry backoff.

Sizing: a single in-flight request is the setting that cannot be wrong,
whatever plan the account is on. Raise _MAX_CONCURRENT_REQUESTS only after
confirming a higher concurrency allowance with Z.ai.
"""

from __future__ import annotations

import email.utils
import json
import random
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

import httpx

from Middleware import step_logger

# At most this many GLM requests in flight across the whole process. One is
# the only value that is safe without knowing the account's concurrency
# allowance, and costs nothing at this volume — see the module docstring.
_MAX_CONCURRENT_REQUESTS = 1

# Floor on the gap between one request STARTING and the next. Guards the
# other half of a rate limit — requests per minute — which a concurrency
# limit of one does not by itself bound (ten quick calls in a row would
# still be ten calls in ten seconds).
_MIN_SECONDS_BETWEEN_REQUESTS = 1.5

# Cool-down applied to every caller after a 429 that carried no usable
# `Retry-After`, and the ceiling on one that did. The ceiling matters: a
# server that asks for an hour must not park a pipeline thread for an hour
# — the durable retry queue (Service/WhatsAppDataFetchingService/
# pending_batch_store.py) is the right place for a wait that long, and it
# picks the batch up automatically.
_DEFAULT_COOLDOWN_SECONDS = 15.0
_MAX_COOLDOWN_SECONDS = 180.0

# Z.ai's own error codes, read out of the 429's JSON body. They mean very
# different things and deserve very different waits:
#   1302 — requests arriving too fast (per-minute rate)
#   1303 — too many requests AT ONCE (concurrency) — the one we were hitting
#   1304 — today's allowance is used up; no backoff inside a batch can fix
#          that, so it is handed to the durable queue quickly instead
_CODE_RATE_TOO_FAST = "1302"
_CODE_TOO_CONCURRENT = "1303"
_CODE_DAILY_LIMIT_REACHED = "1304"

_COOLDOWN_BY_CODE = {
    _CODE_RATE_TOO_FAST: 30.0,
    _CODE_TOO_CONCURRENT: 10.0,
    _CODE_DAILY_LIMIT_REACHED: _MAX_COOLDOWN_SECONDS,
}

_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_REQUESTS)
_state_lock = threading.Lock()
_earliest_next_start = 0.0
_cooldown_until = 0.0
_cooldown_note = ""
_waiting_callers = 0


@contextmanager
def slot(description: str) -> Iterator[None]:
    """Holds one of the process's GLM request slots for the duration of the
    block, after waiting out any cool-down another caller is serving.

    Always released, including when the request inside raises — which is
    what lets a caller sleep out its retry backoff without holding the slot
    shut against everyone else."""
    global _waiting_callers

    with _state_lock:
        _waiting_callers += 1
        queued_ahead = _waiting_callers - 1
    if queued_ahead > 0:
        step_logger.info(
            f"Holding {description} — {queued_ahead} other GLM request(s) already queued. "
            "Requests go out one at a time on purpose; sending them together is what Z.ai "
            "answers with HTTP 429."
        )

    _slots.acquire()
    try:
        _wait_for_clear_window()
        yield
    finally:
        _slots.release()
        with _state_lock:
            _waiting_callers -= 1


def _wait_for_clear_window() -> None:
    """Blocks until both the shared cool-down has expired and the minimum
    spacing since the previous request has elapsed, then claims this
    request's start time. Re-checks in a loop rather than sleeping once: a
    second 429 landing while we wait EXTENDS the cool-down, and this has to
    honour the extension."""
    global _earliest_next_start

    while True:
        with _state_lock:
            now = time.monotonic()
            wait_for = max(_cooldown_until - now, _earliest_next_start - now)
            note = _cooldown_note
            if wait_for <= 0:
                _earliest_next_start = now + _MIN_SECONDS_BETWEEN_REQUESTS
                return
        if wait_for > 2.0:
            step_logger.info(
                f"Pausing GLM requests for {wait_for:.0f}s before the next one"
                + (f" — {note}" if note else "")
            )
        # Capped so an extended cool-down is picked up on the next pass
        # rather than being slept straight through.
        time.sleep(min(wait_for, 5.0))


def note_rate_limited(response: Optional[httpx.Response], retry_number: int) -> float:
    """Records that Z.ai rejected a request with HTTP 429 and returns how
    long THIS caller should wait before trying again. Also parks every other
    caller for the same period, so the pipeline stops competing with itself
    the moment the first thread discovers the limit.

    `retry_number` is 1 for the first 429 of a request, 2 for the second and
    so on; it only ever lengthens the wait, never shortens one the server
    asked for."""
    global _cooldown_until, _cooldown_note

    code = _error_code(response)
    server_asked_for = _retry_after_seconds(response)
    baseline = _COOLDOWN_BY_CODE.get(code or "", _DEFAULT_COOLDOWN_SECONDS)
    # Escalates across repeated 429s for the same request, then is jittered
    # so two threads released by the same cool-down don't re-collide in
    # lockstep.
    escalated = baseline * (2 ** max(retry_number - 1, 0))
    wait_for = min(max(server_asked_for or 0.0, escalated), _MAX_COOLDOWN_SECONDS)
    wait_for += random.uniform(0.0, min(3.0, wait_for * 0.25))

    with _state_lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + wait_for)
        _cooldown_note = describe(response)
    return wait_for


def is_daily_limit(response: Optional[httpx.Response]) -> bool:
    """True when a 429 says today's allowance is gone (Z.ai code 1304).
    Retrying inside the batch cannot help; the caller hands it straight to
    the durable queue instead, which will still have it tomorrow."""
    return _error_code(response) == _CODE_DAILY_LIMIT_REACHED


def describe(response: Optional[httpx.Response]) -> str:
    """Z.ai's own explanation for a rejection, for the log line. The body is
    the only thing that distinguishes "too many at once" from "out of
    credit" — an HTTP status alone never did, which is why the old 429 log
    line could only guess."""
    code = _error_code(response)
    message = _error_message(response)
    known = {
        _CODE_RATE_TOO_FAST: "Z.ai says requests are arriving too fast",
        _CODE_TOO_CONCURRENT: "Z.ai says too many requests are running at once",
        _CODE_DAILY_LIMIT_REACHED: "Z.ai says this account's daily request allowance is used up",
    }.get(code or "")
    parts = [part for part in (known, message) if part]
    if code and not known:
        parts.append(f"Z.ai code {code}")
    return " — ".join(parts) if parts else "Z.ai gave no reason"


def _body(response: Optional[httpx.Response]) -> dict:
    """The rejection's JSON body, or {} for anything unreadable. Never
    raises: this only ever improves a log line and chooses a wait."""
    if response is None:
        return {}
    try:
        parsed = json.loads(response.text or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_code(response: Optional[httpx.Response]) -> Optional[str]:
    body = _body(response)
    error = body.get("error")
    if isinstance(error, dict) and error.get("code") is not None:
        return str(error["code"])
    code = body.get("code")
    return str(code) if code is not None else None


def _error_message(response: Optional[httpx.Response]) -> str:
    body = _body(response)
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"].strip()
    message = body.get("message")
    return message.strip() if isinstance(message, str) else ""


def _retry_after_seconds(response: Optional[httpx.Response]) -> Optional[float]:
    """`Retry-After`, in either of the two forms HTTP allows (a number of
    seconds, or an absolute date). None when absent or unparseable — the
    caller then falls back to its own backoff."""
    if response is None:
        return None
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(when.timestamp() - time.time(), 0.0)
