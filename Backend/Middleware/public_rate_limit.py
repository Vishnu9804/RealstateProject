"""A small, in-process rate limit on the handful of endpoints an anonymous
stranger can call.

Everything else this API exposes is reached from the internal tool by the
client's own staff. These few are not: the public site (LandingPage/) posts
enquiries, requirements and verification codes from whatever browser happens
to load it, with no account and no session behind them. That is the whole
attack surface, and until now it had no ceiling at all — one script could
sit on /landing/leads and make this backend write a row, re-embed a set of
requirements and rewrite a client's cached match table as fast as the
network allowed, on a database that is billed by the hour it stays awake and
by the bytes it sends.

The per-identity guards elsewhere (one enquiry per number per property, a
capped number of requirement submissions, the OTP's own per-number limits)
are the RIGHT protections, and they are why the ceiling here can be so
generous. But each of them has to read the database to decide, so each of
them still costs something per request. This is the layer in front: it
answers from memory, so a flood is refused without a single query.

Deliberately NOT a general-purpose limiter:

  - Only the routes in _LIMITS are covered. Nothing the internal tool calls
    is touched, so no dashboard action can ever be throttled.
  - The budgets are set for a genuine person, with an enormous margin. A
    real visitor fills in one form and asks for at most a couple of codes;
    the cap is dozens per window. Several people behind one office or
    carrier NAT still fit comfortably inside it — that margin is the point,
    because the cost of a false positive here is a real customer being
    turned away.
  - Read endpoints (the property list, the areas) are not limited: those
    are already answered from an in-memory snapshot behind an ETag, so
    repeating them costs no database traffic at all.

In-process and per-worker, like every other piece of short-lived state in
this project (form tokens, OTPs, the Instagram nudge cooldown). It is a cost
ceiling, not a security boundary — the security boundaries are the verified
number and the per-identity caps, which are durable.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# (max requests, window in seconds) per client address, keyed by the path
# prefix the rule applies to. Both windows are long rather than per-second:
# a burst from one person tapping a button twice is normal and must not be
# refused, while a sustained flood is exactly what a long window catches.
_LIMITS: Tuple[Tuple[str, int, int], ...] = (
    # Enquiries and requirement submissions — the two that write.
    ("/api/landing/leads", 40, 600),
    ("/api/whatsapp-inquiry/form", 40, 600),
    # Verification. otp_service already caps sends per NUMBER (5/hour) and
    # verify attempts per code; this caps the number of distinct numbers one
    # address can work through, which is the part a per-number rule cannot
    # see.
    ("/api/whatsapp-inquiry/verify", 60, 600),
)

# Hard ceiling on how many addresses are tracked at once. Without it, a
# flood from spoofed/rotating addresses would grow this dict without bound —
# turning a request limiter into a memory leak, which is a worse version of
# the problem it exists to solve. When full, the oldest-touched entries go;
# an evicted attacker simply starts their window again, which still leaves
# them rate limited, just from zero.
_MAX_TRACKED = 20_000

_lock = threading.Lock()
# (rule prefix, client key) -> timestamps of the requests inside the window
_hits: Dict[Tuple[str, str], Deque[float]] = {}
_last_touched: Dict[Tuple[str, str], float] = {}

_RETRY_MESSAGE = (
    "We've had a lot of requests from your connection just now, so please give it a minute and try "
    "again. Nothing you've already sent us is lost."
)


def _rule_for(path: str) -> Optional[Tuple[str, int, int]]:
    for prefix, limit, window in _LIMITS:
        if path.startswith(prefix):
            return prefix, limit, window
    return None


def _client_key(request: Request) -> str:
    """The address this request came from.

    X-Forwarded-For's FIRST entry is the original client when a reverse
    proxy or CDN sits in front of this app, which is how it will be
    deployed. It is trivially spoofable, and that is fine for what this is:
    someone who forges it gets a different bucket, not a bypass of anything
    that protects data — the per-identity caps and the verified number are
    what do that, and neither of them can be forged by a header.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _allow(rule: Tuple[str, int, int], key: str) -> bool:
    prefix, limit, window = rule
    now = time.monotonic()
    bucket_key = (prefix, key)
    with _lock:
        bucket = _hits.get(bucket_key)
        if bucket is None:
            if len(_hits) >= _MAX_TRACKED:
                _evict_locked()
            bucket = deque()
            _hits[bucket_key] = bucket
        # Only the front of the deque can be expired, so this pops at most
        # as many entries as have actually aged out — never a full scan.
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        _last_touched[bucket_key] = now
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _evict_locked() -> None:
    """Drops the least recently touched tenth of the table. Called only with
    _lock held, and only when the table is full."""
    victims = sorted(_last_touched, key=_last_touched.get)[: max(1, _MAX_TRACKED // 10)]
    for victim in victims:
        _hits.pop(victim, None)
        _last_touched.pop(victim, None)


class PublicRateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the rules above, and gets out of the way for everything else.

    Non-covered paths (every internal endpoint, and the public READ
    endpoints) do one string-prefix check and go straight through, so this
    costs nothing measurable on the routes it does not police. OPTIONS is
    skipped so a CORS preflight can never be what uses up a visitor's
    budget.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        rule = _rule_for(request.url.path)
        if rule is None or _allow(rule, _client_key(request)):
            return await call_next(request)
        # 429 with Retry-After, and a message written for a person rather
        # than a machine — the public site shows response text to visitors.
        return JSONResponse(
            status_code=429,
            content={"detail": _RETRY_MESSAGE},
            headers={"Retry-After": str(rule[2])},
        )
