"""Conditional-request (ETag / 304) support for the read endpoints whose
answers rarely change.

WHAT THIS BUYS

A browser already keeps every response it has seen. What it needs from the
server is (a) permission to reuse it and (b) a cheap way to ask "is this
still current?". That is all an ETag is: a short string identifying the
version of a response. The browser stores it, sends it back as
`If-None-Match` on the next request, and if it still matches, the server
replies `304 Not Modified` with NO BODY — the browser then serves the copy
it already had.

For this application that is the difference between shipping a property's
photos (base64, frequently megabytes) on every single view, and shipping
about a hundred bytes. The landing page's published list, the property
list, and every photo payload are all things a visitor asks for repeatedly
and that usually have not changed since they last asked.

WHY IT REDUCES DATABASE COST, NOT JUST BANDWIDTH

Only because of where the version string comes from. Every validator used
here is computed from the in-memory property snapshot (Service/
WhatsAppDataFetchingService/property_snapshot.py), never from a query — so
answering "nothing has changed" costs no database work at all. An ETag that
had to query Postgres to prove itself would save bandwidth and nothing
else, which is the trap this module exists to avoid.

WHY `no-cache` AND NOT A LIFETIME

`Cache-Control: no-cache` is widely misread: it does not mean "do not
store", it means "store it, but always ask before reusing it". That is
exactly the guarantee this application needs — a sold property must never
linger on the public site, and an edited price must never be shown stale —
while still removing essentially all of the repeated transfer. A `max-age`
lifetime would be faster still, and would also mean serving data known to
be out of date for as long as that lifetime lasted. Correctness first; the
saving is nearly identical either way.

WEAK ETAGS

Every tag here is weak (`W/"..."`). A strong tag asserts byte-for-byte
identity, which is a promise this server cannot keep — responses pass
through GZipMiddleware, so the same data can legitimately arrive with
different bytes. Weak tags assert semantic equivalence, which is precisely
what is being claimed, and browsers accept them for exactly this purpose
on GET requests.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Request, Response

# Responses are per-user data (the admin dashboard) or public data (the
# landing page). The distinction matters to anything BETWEEN the browser and
# this server — a proxy or CDN may reuse a "public" response for someone
# else, and must never do that with the dashboard's.
_PRIVATE = "private, no-cache"
_PUBLIC = "public, no-cache"


def build_etag(*parts: object) -> str:
    """A weak ETag over whatever uniquely identifies this response's
    version. Parts are hashed rather than concatenated raw so a validator
    can safely be built from values containing quotes, spaces or newlines
    (an ETag is a quoted token, and a raw timestamp or free-text version
    string would otherwise be able to malform the header)."""
    digest = hashlib.sha1("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f'W/"{digest}"'


def is_unchanged(request: Request, etag: str) -> bool:
    """Whether the browser already holds this exact version.

    `If-None-Match` may carry several tags, and a cached response revalidated
    through a proxy can come back prefixed `W/`. Comparison is therefore by
    membership over a split list, using the weak-comparison rule the spec
    defines for it: the `W/` prefix is ignored on both sides. `*` means "any
    stored version", which for a conditional GET means the browser has one.
    """
    header = request.headers.get("if-none-match")
    if not header:
        return False
    candidates = {value.strip() for value in header.split(",")}
    if "*" in candidates:
        return True
    return _opaque(etag) in {_opaque(value) for value in candidates}


def _opaque(value: str) -> str:
    return value[2:] if value.startswith("W/") else value


def not_modified(etag: str, *, public: bool = False) -> Response:
    """The 304 itself.

    Built as a bare Response rather than by raising, so the caller reads as
    an ordinary early return. It carries no body by construction: a 304 with
    one is malformed, and the whole point is that no body crosses the wire.

    The validator headers are repeated here deliberately — a 304 refreshes
    the browser's stored copy, so omitting them would let the cached entry's
    freshness information decay away and turn the next request back into a
    full download.
    """
    return Response(
        status_code=304,
        headers={"ETag": etag, "Cache-Control": _PUBLIC if public else _PRIVATE},
    )


def mark(response: Response, etag: str, *, public: bool = False) -> None:
    """Tags a normal 200 so the browser can revalidate it next time. Without
    this on the 200, there is nothing for a later request to match against
    and every request stays a full download."""
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = _PUBLIC if public else _PRIVATE


def conditional(
    request: Request,
    response: Response,
    etag: Optional[str],
    *,
    public: bool = False,
) -> Optional[Response]:
    """The whole exchange in one call: returns a 304 to return immediately,
    or None meaning "carry on and build the real answer" — having already
    tagged the outgoing response.

    `etag` may be None, which means this particular resource has no cheap
    version to prove (e.g. a property that falls outside the in-memory
    snapshot). That case deliberately degrades to plain, uncached behaviour
    rather than guessing: a validator that cannot be trusted to change when
    the data changes is far worse than none at all — it would pin a stale
    copy in the browser indefinitely.
    """
    if etag is None:
        return None
    if is_unchanged(request, etag):
        return not_modified(etag, public=public)
    mark(response, etag, public=public)
    return None
