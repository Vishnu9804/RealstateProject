"""Per-user debounce buffer for the inquiry-handling pipeline.

Unlike Service/WhatsAppDataFetchingService/message_buffer_service.py (one
global batch, flushed by size-or-time), this buffers messages PER SENDER
PHONE NUMBER, and flushes on inactivity rather than a fixed window: every
new message from a given number cancels and restarts that number's own
countdown timer, so the batch is only handed off once that person has
actually stopped sending fragmented follow-ups (e.g. "Hello" / "I wanted a
rented villa" / "budget 20k" sent seconds apart).

This isolation is deliberate and load-bearing: a per-number buffer/timer
pair means one user's in-flight message stream can never be flushed
together with, delayed by, or interleaved into another user's batch — a mix
here would show up downstream as answering (or matching properties to) the
wrong client. Each number's buffer is independent; a burst from number A
never resets or extends number B's timer.

Deterministic buffering, not decision-making — belongs in Service/, not
Agent/ (reserved for actual LLM-driven code).
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List

from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage

# Safety net, not a normal trigger: caps how many messages one number can
# pile up before we force a flush regardless of the inactivity timer. Real
# conversations never get close to this — it exists only so a pathological
# case (someone rapid-firing messages with <10s gaps indefinitely) can't
# grow one user's buffer, and the eventual LLM prompt, without bound.
MAX_MESSAGES_PER_BATCH = 40


class InquiryBufferService:
    def __init__(
        self,
        on_batch_ready: Callable[[str, List[InquiryChatMessage]], None],
        inactivity_window_seconds: float,
    ):
        self._on_batch_ready = on_batch_ready
        self._inactivity_window_seconds = inactivity_window_seconds

        self._lock = threading.Lock()
        self._buffers: Dict[str, List[InquiryChatMessage]] = {}
        self._timers: Dict[str, threading.Timer] = {}

    def add_message(self, message: InquiryChatMessage) -> None:
        phone = message.sender_phone
        with self._lock:
            bucket = self._buffers.setdefault(phone, [])
            bucket.append(message)

            if len(bucket) >= MAX_MESSAGES_PER_BATCH:
                self._flush_locked(phone)
                return

            # Every message — first or not — (re)starts this number's own
            # countdown, and only this number's: touching one phone's timer
            # never affects any other phone's buffer or timer.
            self._start_timer_locked(phone)

    def pending_count(self) -> int:
        """Total messages currently buffered across every phone number."""
        with self._lock:
            return sum(len(bucket) for bucket in self._buffers.values())

    def active_user_count(self) -> int:
        """How many distinct phone numbers currently have a pending batch."""
        with self._lock:
            return len(self._buffers)

    def flush_now(self, phone: str) -> None:
        """Flushes one number's buffer immediately, even if its inactivity
        timer hasn't fired yet. Exposed for manual/API control."""
        with self._lock:
            self._flush_locked(phone)

    def _start_timer_locked(self, phone: str) -> None:
        self._cancel_timer_locked(phone)
        timer = threading.Timer(
            self._inactivity_window_seconds, self._handle_timer_fired, args=(phone,)
        )
        timer.daemon = True
        self._timers[phone] = timer
        timer.start()

    def _handle_timer_fired(self, phone: str) -> None:
        with self._lock:
            self._flush_locked(phone)

    def _cancel_timer_locked(self, phone: str) -> None:
        timer = self._timers.pop(phone, None)
        if timer is not None:
            timer.cancel()

    def _flush_locked(self, phone: str) -> None:
        bucket = self._buffers.pop(phone, None)
        self._cancel_timer_locked(phone)
        if not bucket:
            return
        step_logger.info(f"[Inquiry] Buffer flushed for {phone}: {len(bucket)} message(s)")
        # Run the callback on its own thread, outside the lock: the next
        # step wires this to an LLM call, which must never block new
        # messages — from this or any other number — from being buffered
        # while it's in flight.
        threading.Thread(
            target=self._on_batch_ready,
            args=(phone, bucket),
            name=f"inquiry-batch-flush-{phone}",
            daemon=True,
        ).start()
