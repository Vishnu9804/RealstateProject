"""A per-identity daily allowance, shared by every inbound channel.

THE PROBLEM THIS SOLVES. Everything else in this project is bounded by
something: a poll interval, a batch size, a database row. Inbound messages
are not. One person with a phone can send a thousand WhatsApp messages in a
minute, and before this every one of them was buffered, handed to Gemini,
and answered — a thousand LLM calls, a thousand database lookups and a
thousand outbound sends, all paid for by the business being messaged. The
same is true of Instagram comments and shared reels. No amount of
downstream efficiency fixes that shape of problem; the only fix is to stop
counting at some point, and the only place that can be done for free is at
the very moment the message arrives.

WHAT IS COUNTED. Each (bucket, identity) pair gets an allowance per day:

  - whatsapp_inquiry — messages AND words, from one personal number.
    Whichever runs out first closes the door for the rest of the day, and
    the count is of EVERY message, not just property-related ones: the
    expensive part (the classification call that decides which it was)
    happens before anyone knows, so a limit that only counted the
    property-related ones would not limit the cost at all.
  - instagram_comment / instagram_dm — from one Instagram account.

THE CROSSING MESSAGE IS ALWAYS ACCEPTED. The check is "is this identity
already out of allowance?", asked before the message is counted — never
"would this message take them over?". So someone whose first message of the
day is a 300-word description of what they want is answered, and only what
comes AFTER it is refused. Refusing that message would be refusing the best
enquiry of the day for being too thorough, which is the exact opposite of
the point. The cost of the concession is bounded by one message.

THE WINDOW RESETS AT 6 AM IST, and resets to EMPTY. Messages that arrived
while an identity was out of allowance are not counted, not stored, and not
replayed when the window turns over — the new day starts from whatever
arrives after it, with no backlog. That falls out of the design rather than
being arranged: a refused message is dropped where it arrives, so there is
never anything held anywhere to replay.

Reset is computed, not scheduled. Every read works out which window "now"
falls in and clears a stale entry on the spot, so there is no timer to
drift, nothing to miss if the process was asleep at 6 AM, and no background
thread at all.

NO DATABASE, DELIBERATELY. This is counter state that is worthless the
moment the day turns over, and it is read on the hottest path there is —
every single inbound message. Persisting it would mean a read and a write
per message against a metered database, to protect against spending money
on a metered database. On a restart every identity starts the day again,
which costs at most one extra window's allowance and is self-correcting.

Memory is bounded by _MAX_TRACKED with least-recently-used eviction, for
the same reason Middleware/public_rate_limit.py bounds its table: an
unbounded dict keyed by "whoever messaged us" is itself a way to exhaust a
server, and a limiter that can be turned into a memory leak has not limited
anything. Evicting an entry gives that identity a fresh allowance, so
eviction order matters: the LEAST recently active identity is dropped,
which is precisely the one least likely to be mid-flood.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Dict, NamedTuple, Optional

# Fixed +5:30, not a tz-database lookup — IST has no DST, so the offset is
# exact year-round. Same choice, for the same reason, as
# Service/ClientPropertyMatchingService/scheduled_recompute_service.py.
_IST = timezone(timedelta(hours=5, minutes=30))
_RESET_HOUR_IST = 6

# One identity per tracked (bucket, identity) pair. 20k is far beyond any
# real day's traffic for this business, and small enough to be irrelevant
# in memory.
_MAX_TRACKED = 20_000


class Limits(NamedTuple):
    """`max_words` of 0 means this bucket does not count words at all —
    an Instagram comment is one event, and measuring its length would cap
    thoughtfulness rather than volume."""

    max_messages: int
    max_words: int = 0


class Decision(NamedTuple):
    """`first_refusal` is true on the ONE call that flips an identity from
    inside its allowance to outside it, and never again that day. It is
    what lets a channel send a single courtesy message explaining the
    silence: a real person who has been chatty deserves to know their
    messages arrived, and a flood must not be handed an unlimited way to
    make this server send messages. Exactly one, per identity, per day.

    `reason` is "messages" or "words" — which allowance ran out — purely
    so the log says something useful."""

    allowed: bool
    first_refusal: bool = False
    reason: str = ""


class _Entry:
    __slots__ = ("window", "messages", "words", "refused")

    def __init__(self, window: str) -> None:
        self.window = window
        self.messages = 0
        self.words = 0
        # Whether the "you have been told" message has already gone out in
        # THIS window. Reset with everything else at 6 AM.
        self.refused = False


_lock = threading.Lock()
_entries: "OrderedDict[tuple, _Entry]" = OrderedDict()


def current_window() -> str:
    """Identifies the day-window `now` falls in, as the date of the 6 AM
    IST boundary that opened it. Anything before 06:00 IST still belongs to
    the window that opened yesterday morning, which is what makes a message
    at 2 AM count against the evening it is a continuation of rather than
    starting a fresh allowance in the middle of the night."""
    now = datetime.now(_IST)
    boundary = now.replace(hour=_RESET_HOUR_IST, minute=0, second=0, microsecond=0)
    if now < boundary:
        boundary -= timedelta(days=1)
    return boundary.date().isoformat()


def _entry_locked(key: tuple, window: str) -> _Entry:
    entry = _entries.get(key)
    if entry is None:
        if len(_entries) >= _MAX_TRACKED:
            # Least recently touched first — see the module docstring on why
            # the order is the part that matters.
            _entries.popitem(last=False)
        entry = _Entry(window)
        _entries[key] = entry
    elif entry.window != window:
        # The window turned over since this identity was last seen. Reset in
        # place rather than deleting: this is the entire reset mechanism,
        # and it costs one string comparison on a path that was going to
        # touch this entry anyway.
        entry.window = window
        entry.messages = 0
        entry.words = 0
        entry.refused = False
    _entries.move_to_end(key)
    return entry


def is_exhausted(bucket: str, identity: str, limits: Limits) -> bool:
    """Whether this identity is already out of allowance — a pure read that
    consumes nothing.

    Exists so a caller can skip work BEFORE doing anything that costs
    money. Instagram's poller uses it to drop a flooder's events without so
    much as a database lookup, on every cycle, for the rest of the day."""
    if limits.max_messages <= 0:
        return False
    key = (bucket, identity)
    window = current_window()
    with _lock:
        entry = _entries.get(key)
        if entry is None or entry.window != window:
            return False
        return _is_exhausted_locked(entry, limits)


def _is_exhausted_locked(entry: _Entry, limits: Limits) -> bool:
    if entry.messages >= limits.max_messages:
        return True
    return limits.max_words > 0 and entry.words >= limits.max_words


def consume(bucket: str, identity: str, limits: Limits, words: int = 0) -> Decision:
    """Books one message against this identity's allowance.

    Order is deliberate and is the whole of the "crossing message is
    accepted" rule: the allowance is tested BEFORE this message is added,
    so the message that takes someone to (or past) a limit is allowed
    through and only the next one is refused.

    A refusal counts nothing — not the message, not its words. There is no
    way to sink an identity deeper into a refusal by continuing to send,
    which is what makes the 6 AM reset honest: a day's worth of refused
    flooding leaves exactly the same state behind as one refused message.
    """
    if limits.max_messages <= 0:
        return Decision(allowed=True)
    key = (bucket, identity)
    window = current_window()
    with _lock:
        entry = _entry_locked(key, window)
        if _is_exhausted_locked(entry, limits):
            first = not entry.refused
            entry.refused = True
            return Decision(
                allowed=False,
                first_refusal=first,
                reason="messages" if entry.messages >= limits.max_messages else "words",
            )
        entry.messages += 1
        entry.words += max(0, words)
        return Decision(allowed=True)


def count_words(text: Optional[str]) -> int:
    """Whitespace-separated runs, which is what a person means by "words".

    Never zero for a message that has any content at all: a single
    unbroken 5,000-character string is one "word" by this measure, and
    counting it as nothing would leave the word allowance blind to exactly
    the shape of message most likely to be abusive. The message allowance
    still bounds those, and the length cap below is what bounds one
    message's contribution."""
    if not text:
        return 0
    return len(text.split())


def snapshot() -> Dict[str, int]:
    """How many identities are currently being tracked — for the status
    endpoint, so this is observable without reaching into module state."""
    with _lock:
        return {"tracked_identities": len(_entries)}


def reset_all() -> None:
    """Clears every counter. Only for tests — nothing in the running
    application calls this, because the 6 AM window is the only reset the
    application has any business performing."""
    with _lock:
        _entries.clear()
