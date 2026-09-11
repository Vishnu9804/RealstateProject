"""Mints and resolves the single-use-ish, identity-scoped tokens used in
the registration/update form link — sent over WhatsApp (see
inquiry_pipeline_service.py) or DMed on Instagram (see
Service/InstagramInquiryHandlingService/instagram_polling_service.py). A
token exists purely to bind "whoever opens this link" back to the one
identity it was issued for — the form endpoint must trust ONLY the
identity a token was minted for, never anything typed into the page
itself, otherwise one visitor could overwrite another's requirements just
by editing the URL. That's the actual mechanism behind the feature spec's
"must never mix one user's requirements with another user's" requirement,
applied to the form link specifically.

`identity` is a phone number for channel="whatsapp", an Instagram user id
for channel="instagram" — the two channels' identities live in entirely
different namespaces (E.164 numbers vs Instagram's numeric ids), so there
is no risk of collision between them even though both pass through this
one `identity: str` field.

In-memory only for now — tokens are short-lived and cheap to reissue, so
losing outstanding ones on a restart is an acceptable cost, unlike the
durable client data in Database/client_repository.py.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Literal, NamedTuple, Optional, Tuple

_TOKEN_TTL_SECONDS = 24 * 60 * 60  # 24 hours

Channel = Literal["whatsapp", "instagram"]


class _TokenEntry(NamedTuple):
    channel: Channel
    identity: str
    expires_at: float


# A ceiling on how many live tokens are held at once, and a sweep to keep
# the table near it.
#
# Every token has a 24-hour life, but until now the only thing that ever
# removed one was somebody opening that exact link and finding it expired —
# so a token nobody ever clicked stayed in memory for the life of the
# process. One per welcome message, one per update link, one per Instagram
# DM sequence, forever. That is a slow leak in a process meant to run for
# months, and a leak reachable from outside is a way to bring a server
# down without ever attacking it directly.
#
# The sweep below runs only when the table is over the ceiling, and drops
# genuinely expired entries first — in the normal case that is all of the
# excess, and nothing live is touched. Only if the table is STILL over the
# ceiling after that (which would mean tens of thousands of unexpired
# tokens, i.e. an attack rather than a Tuesday) are the oldest live ones
# dropped, oldest first, because the newest link is the one somebody is
# most likely about to open.
_MAX_LIVE_TOKENS = 50_000

_lock = threading.Lock()
_tokens: Dict[str, _TokenEntry] = {}


def _sweep_locked() -> None:
    if len(_tokens) <= _MAX_LIVE_TOKENS:
        return
    now = time.monotonic()
    for token in [t for t, entry in _tokens.items() if entry.expires_at < now]:
        del _tokens[token]
    if len(_tokens) <= _MAX_LIVE_TOKENS:
        return
    for token in sorted(_tokens, key=lambda t: _tokens[t].expires_at)[: len(_tokens) - _MAX_LIVE_TOKENS]:
        del _tokens[token]


def issue_token(channel: Channel, identity: str) -> str:
    """Mints a fresh token bound to (channel, identity). Each call is a
    new, independent token — nothing about a previously issued one for the
    same identity is reused, extended, or invalidated."""
    token = secrets.token_urlsafe(24)
    with _lock:
        _tokens[token] = _TokenEntry(channel=channel, identity=identity, expires_at=time.monotonic() + _TOKEN_TTL_SECONDS)
        _sweep_locked()
    return token


def resolve_token(token: str) -> Optional[Tuple[Channel, str]]:
    """Returns the (channel, identity) `token` was issued for, or None if
    it's unknown or has expired. Never raises."""
    with _lock:
        entry = _tokens.get(token)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            del _tokens[token]
            return None
        return entry.channel, entry.identity
