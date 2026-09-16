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

Durability (`pipeline=`)
------------------------
A buffer can hold a message for up to a full hour before its batch is worth
sending. Everything in it used to live only in this process's memory, so
stopping the server — a deploy, a crash, a Ctrl+C — threw away every message
waiting in it. Pass `pipeline` and that window is covered: the buffer's
contents are mirrored to disk on every message and restored on the next start
(see Service/WhatsAppDataFetchingService/pending_batch_store.py).

The hand-off at flush time is ordered so there is no instant where a message
exists in neither place:

    1. the batch is CLAIMED as a durable record of its own,
    2. only then is it dropped from the buffer's snapshot,
    3. only then does the pipeline thread start work on it.

If the process dies at any point in that sequence, the messages are still on
disk in exactly one of the two forms, and the next start picks them up.

Leave `pipeline` unset and this class behaves exactly as it always did,
touching no disk at all.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import pending_batch_store

DEFAULT_BATCH_SIZE = 10
DEFAULT_BATCH_WINDOW_SECONDS = 60 * 60  # 1 hour


class MessageBufferService:
    def __init__(
        self,
        on_batch_ready: Callable[[List[WhatsAppChatMessage]], None],
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_window_seconds: float = DEFAULT_BATCH_WINDOW_SECONDS,
        pipeline: Optional[str] = None,
    ):
        self._on_batch_ready = on_batch_ready
        self._batch_size = batch_size
        self._batch_window_seconds = batch_window_seconds
        # Which pipeline's messages these are ("property"/"requirement"), used
        # both as this buffer's snapshot name and as the pipeline a restored
        # batch belongs to. None disables persistence entirely.
        self._pipeline = pipeline

        self._lock = threading.Lock()
        self._buffer: List[WhatsAppChatMessage] = []
        self._flush_timer: Optional[threading.Timer] = None
        # Wall-clock epoch seconds, not monotonic: it is written to disk and
        # has to still mean something after a restart.
        self._window_started_at: Optional[float] = None

    def add_message(self, message: WhatsAppChatMessage) -> None:
        with self._lock:
            self._buffer.append(message)
            if len(self._buffer) == 1:
                # First message of a fresh batch — start its countdown.
                self._window_started_at = time.time()
                self._start_timer_locked(self._batch_window_seconds)
            # Saved BEFORE the size check below, so a message is on disk even
            # if the flush it triggers is the thing that fails. The snapshot
            # is the whole buffer every time, so the previous batch's messages
            # are gone from it the moment they are handed off and the next
            # batch simply takes their place — the file never accumulates.
            self._save_locked()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()

    def restore_from_disk(self) -> None:
        """Puts back whatever this buffer was holding when the process last
        stopped, and serves out the REMAINDER of that batch's original window
        rather than starting a fresh one — so a message cannot be pushed back
        by another full hour every time the server restarts. A window that has
        already elapsed flushes straight away.

        Called once, right after construction, before any new message can
        arrive. Does nothing (and touches no disk) when persistence is off."""
        if self._pipeline is None:
            return
        messages, window_started_at = pending_batch_store.load_buffer(self._pipeline)
        if not messages:
            return

        from Middleware import step_logger

        # A message that a batch record already owns is that record's job, not
        # this buffer's — see pending_batch_store.message_ids_in_records for
        # the one instant in which a message can be in both files.
        already_owned = pending_batch_store.message_ids_in_records()
        if already_owned:
            kept = [message for message in messages if message.message_id not in already_owned]
            if len(kept) != len(messages):
                step_logger.info(
                    f"{len(messages) - len(kept)} restored {self._pipeline} message(s) had already been "
                    "handed to a batch of their own before the last shutdown — leaving them to it rather "
                    "than structuring them twice."
                )
            messages = kept
        if not messages:
            pending_batch_store.clear_buffer(self._pipeline)
            return

        with self._lock:
            self._buffer = list(messages)
            self._window_started_at = window_started_at or time.time()
            remaining = (self._window_started_at + self._batch_window_seconds) - time.time()
            if remaining <= 0 or len(self._buffer) >= self._batch_size:
                step_logger.warn(
                    f"Restored {len(messages)} buffered {self._pipeline} message(s) from the previous run — "
                    "their batch window had already elapsed, so they are being structured now. "
                    "None of them were lost."
                )
                self._flush_locked()
                return
            step_logger.warn(
                f"Restored {len(messages)} buffered {self._pipeline} message(s) from the previous run — "
                f"they keep the remaining {remaining / 60:.0f} minute(s) of their original batch window. "
                "None of them were lost."
            )
            self._start_timer_locked(remaining)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def flush_now(self) -> None:
        """Flushes whatever is currently buffered immediately, even if
        neither trigger has fired yet. Exposed for manual/API control."""
        with self._lock:
            self._flush_locked()

    def _start_timer_locked(self, seconds: float) -> None:
        self._cancel_timer_locked()
        self._flush_timer = threading.Timer(max(seconds, 0.0), self._handle_timer_fired)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _handle_timer_fired(self) -> None:
        with self._lock:
            self._flush_locked()

    def _cancel_timer_locked(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _save_locked(self) -> None:
        if self._pipeline is None:
            return
        pending_batch_store.save_buffer(self._pipeline, self._buffer, self._window_started_at)

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._window_started_at = None
        self._cancel_timer_locked()

        # Custody passes here, in this order, and the order is the whole
        # point: the batch gets its own durable record FIRST, and only then is
        # it removed from this buffer's snapshot. Dying between the two costs
        # one duplicate retry (which the pipelines' fingerprint check turns
        # into a no-op); doing it the other way round would cost the batch.
        batch_id: Optional[str] = None
        if self._pipeline is not None:
            batch_id = pending_batch_store.claim(self._pipeline, batch)
            self._save_locked()

        # Run the callback on its own thread, outside the lock: the next
        # step wires this to an LLM call, which must never block new
        # messages from being buffered while it's in flight.
        threading.Thread(
            target=self._run_batch, args=(batch, batch_id), name="message-batch-flush", daemon=True
        ).start()

    def _run_batch(self, batch: List[WhatsAppChatMessage], batch_id: Optional[str]) -> None:
        """Runs the batch with its durable record attached to this thread, so
        the pipeline's own claim() finds the record made above instead of
        writing a second one for the same messages. That record is deleted by
        the pipeline the moment the batch is finished."""
        with pending_batch_store.working_on(batch_id):
            self._on_batch_ready(batch)
