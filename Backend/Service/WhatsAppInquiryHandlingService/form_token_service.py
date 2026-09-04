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


_lock = threading.Lock()
_tokens: Dict[str, _TokenEntry] = {}


def issue_token(channel: Channel, identity: str) -> str:
    """Mints a fresh token bound to (channel, identity). Each call is a
    new, independent token — nothing about a previously issued one for the
    same identity is reused, extended, or invalidated."""
    token = secrets.token_urlsafe(24)
    with _lock:
        _tokens[token] = _TokenEntry(channel=channel, identity=identity, expires_at=time.monotonic() + _TOKEN_TTL_SECONDS)
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
