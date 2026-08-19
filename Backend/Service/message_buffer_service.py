"""Buffers qualified property messages (Stage 2 of the pipeline — "Buffered
Processing Window" in the architecture diagram) until either `batch_size`
messages have accumulated or `batch_window_seconds` have elapsed since the
first message in the current batch — whichever happens first — then flushes
them together as a single batch.

This exists purely so the LLM structuring stage (Agent/, next step) can
process up to 10 property messages in a single prompt instead of spending
one request per message. Reaching the size trigger flushes immediately and
resets the timer, matching "Counter 10 Trigger Resets 1-H Timer to 0" in the
diagram.

Deterministic buffering, not decision-making — belongs in Service/, not
Agent/ (reserved for actual LLM-driven code).
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from Model.whatsapp_message import WhatsAppChatMessage

DEFAULT_BATCH_SIZE = 10
DEFAULT_BATCH_WINDOW_SECONDS = 60 * 60  # 1 hour


class MessageBufferService:
    def __init__(
        self,
        on_batch_ready: Callable[[List[WhatsAppChatMessage]], None],
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_window_seconds: float = DEFAULT_BATCH_WINDOW_SECONDS,
    ):
        self._on_batch_ready = on_batch_ready
        self._batch_size = batch_size
        self._batch_window_seconds = batch_window_seconds

        self._lock = threading.Lock()
        self._buffer: List[WhatsAppChatMessage] = []
        self._flush_timer: Optional[threading.Timer] = None

    def add_message(self, message: WhatsAppChatMessage) -> None:
        with self._lock:
            self._buffer.append(message)
            if len(self._buffer) == 1:
                # First message of a fresh batch — start its countdown.
                self._start_timer_locked()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def flush_now(self) -> None:
        """Flushes whatever is currently buffered immediately, even if
        neither trigger has fired yet. Exposed for manual/API control."""
        with self._lock:
            self._flush_locked()

    def _start_timer_locked(self) -> None:
        self._cancel_timer_locked()
        self._flush_timer = threading.Timer(self._batch_window_seconds, self._handle_timer_fired)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _handle_timer_fired(self) -> None:
        with self._lock:
            self._flush_locked()

    def _cancel_timer_locked(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._cancel_timer_locked()
        # Run the callback on its own thread, outside the lock: the next
        # step wires this to an LLM call, which must never block new
        # messages from being buffered while it's in flight.
        threading.Thread(
            target=self._on_batch_ready, args=(batch,), name="message-batch-flush", daemon=True
        ).start()
