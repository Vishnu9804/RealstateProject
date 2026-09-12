"""Answers one question — "is this number already one of our clients?" —
for the PUBLIC number-confirmation flow, while touching the database as
close to never as it can manage.

Why it exists: otp_service.py can skip the 4-digit code entirely for a
number we already hold a client record for (see its AUTO_CONFIRM_KNOWN_
CLIENTS note). That is a lovely thing for a returning client and a
terrible thing to implement naively, because the endpoint asking it is
open to the internet. A plain client_store lookup per request would turn
`POST /whatsapp-inquiry/verify/request` — until now a purely in-memory
route — into one that can be made to hammer a database billed by the hour
it stays awake and by the bytes it sends. Somebody walking a list of ten
thousand numbers through it would do exactly that.

So three things sit in front of the lookup, in order, and each one is
cheaper than the next:

  1. A TTL cache of both answers. The repeat cases this flow actually
     produces — a visitor who mistypes, re-opens the dialog, presses
     Resend, or comes back an hour later — are then free. NEGATIVE answers
     are cached too, and that is the important half: a flood is made of
     numbers we have never heard of, so it is the "no" that has to be
     cheap.
  2. A global ceiling on lookups per minute, shared by every caller. Not
     per-IP: rotating addresses are the whole point of a flood, and the
     thing being protected (a database bill) is global, so the limit has to
     be too.
  3. The lookup itself, which is client_repository.client_exists — a
     primary-key index probe returning one short string.

Degrading is always safe. Over the ceiling, or on any failure at all, the
answer is "not a known client", which simply means the visitor is sent a
code — exactly what happened before this module existed. Nothing here can
turn into a broken confirmation flow; the worst it can do is an OTP that
was not strictly necessary.

In-process and per-worker, like every other short-lived table in this
project (form tokens, OTPs, the public rate limiter). Two workers each
keep their own copy and each spend their own budget; that is fine, because
this is a cost ceiling rather than a correctness mechanism.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Tuple

from Middleware import step_logger

# How long an answer is trusted. Split deliberately:
#
#  - A "yes" is effectively permanent information (client rows are almost
#    never deleted), so it is held for a long time and costs nothing to be
#    slightly stale — the number is auto-confirmed either way.
#  - A "no" is the one that can change, and the change matters: the moment
#    someone becomes a client they should stop being sent codes. Held
#    briefly for that reason — and, in the normal case, not even relied on,
#    because client_store primes this cache on every upsert (see
#    remember below), so the transition is usually observed instantly.
_POSITIVE_TTL_SECONDS = 6 * 60 * 60
_NEGATIVE_TTL_SECONDS = 10 * 60

# Lookups allowed per window, across all callers. Sized for reality rather
# than for the attack: genuine first-time confirmations from distinct
# numbers arrive a handful per minute at most for a business this size, so
# this is orders of magnitude above normal use and still a hard, provable
# cap on what this feature can ever cost.
_LOOKUP_BUDGET = 20
_LOOKUP_WINDOW_SECONDS = 60

# Ceiling on the cache itself, for the same reason otp_service caps its
# tables: a public endpoint plus a dict that only grows is a memory leak
# with extra steps. Expired entries go first; only a cache still over the
# line after that loses live ones, soonest-to-expire first.
_MAX_ENTRIES = 20_000

_lock = threading.Lock()
# phone -> (is_client, expires_at monotonic)
_answers: Dict[str, Tuple[bool, float]] = {}
_lookups: List[float] = []
# When the "budget spent" line was last written. Being over the ceiling
# means a lot of requests are arriving, so logging each one would turn a
# flood of queries we just avoided into a flood of log lines we did not —
# one line per window says the same thing.
_last_budget_log = 0.0


def is_known_client(phone: str) -> bool:
    """True only when we are confident this E.164 number already has a
    client record. Never raises, and answers False whenever it cannot be
    sure — see the module docstring on why that is always the safe way to
    be wrong."""
    global _last_budget_log

    if not phone:
        return False

    now = time.monotonic()
    with _lock:
        cached = _answers.get(phone)
        if cached is not None and cached[1] > now:
            return cached[0]
        if not _spend_budget_locked(now):
            # Over the ceiling. Deliberately NOT cached: this is a
            # statement about our budget, not about this phone number, and
            # writing it down would keep punishing a real client long after
            # the flood stopped.
            if now - _last_budget_log > _LOOKUP_WINDOW_SECONDS:
                _last_budget_log = now
                step_logger.info(
                    "[KnownClient] Lookup budget spent for this window — falling back to sending codes."
                )
            return False

    try:
        # Outside the lock on purpose: this is the one slow line in the
        # module, and holding a global mutex across a network round trip
        # would serialise every concurrent confirmation behind it.
        from Service.WhatsAppInquiryHandlingService import client_store

        exists = client_store.client_exists(phone)
    except Exception as exc:  # noqa: BLE001
        # A database that is down, asleep or misconfigured must never stop
        # someone confirming their number — they just get a code.
        step_logger.error(f"[KnownClient] Lookup failed for {phone}: {exc!r}")
        return False

    remember(phone, exists)
    return exists


def remember(phone: str, is_client: bool) -> None:
    """Records an answer without a lookup. Called after a real lookup, and
    by client_store whenever a client is written or removed — which is what
    keeps a "no" from outliving the moment it stopped being true."""
    if not phone:
        return
    ttl = _POSITIVE_TTL_SECONDS if is_client else _NEGATIVE_TTL_SECONDS
    now = time.monotonic()
    with _lock:
        _answers[phone] = (is_client, now + ttl)
        _sweep_locked(now)


def _spend_budget_locked(now: float) -> bool:
    """One slot out of the per-window allowance, or False when it is gone.
    Called with _lock held."""
    cutoff = now - _LOOKUP_WINDOW_SECONDS
    # The list is append-only in time order, so everything still inside the
    # window is a contiguous tail — this scans the expired head and stops.
    dropped = 0
    for stamp in _lookups:
        if stamp >= cutoff:
            break
        dropped += 1
    if dropped:
        del _lookups[:dropped]
    if len(_lookups) >= _LOOKUP_BUDGET:
        return False
    _lookups.append(now)
    return True


def _sweep_locked(now: float) -> None:
    """Skipped entirely below the ceiling, so it costs one comparison per
    write in the normal case. Called with _lock held."""
    if len(_answers) <= _MAX_ENTRIES:
        return
    for phone in [p for p, (_, expires_at) in _answers.items() if expires_at <= now]:
        del _answers[phone]
    overflow = len(_answers) - _MAX_ENTRIES
    if overflow > 0:
        for phone in sorted(_answers, key=lambda p: _answers[p][1])[:overflow]:
            del _answers[phone]
