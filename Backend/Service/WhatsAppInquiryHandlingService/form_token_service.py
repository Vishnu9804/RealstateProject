"""Mints and resolves the single-use-ish, phone-scoped tokens used in the
registration/update form link sent over WhatsApp (see
inquiry_pipeline_service.py). A token exists purely to bind "whoever opens
this link" back to the one phone number it was issued for — once the form
endpoint exists (a later step), it must trust ONLY the phone number a token
was minted for, never a phone number typed into the page itself, otherwise
one client could overwrite another client's requirements just by editing
the URL. That's the actual mechanism behind the feature spec's "must never
mix one user's requirements with another user's" requirement, applied to
the form link specifically.

In-memory only for now — tokens are short-lived and cheap to reissue, so
losing outstanding ones on a restart is an acceptable cost, unlike the
durable client data in Database/client_repository.py.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, NamedTuple, Optional

_TOKEN_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class _TokenEntry(NamedTuple):
    phone: str
    expires_at: float


_lock = threading.Lock()
_tokens: Dict[str, _TokenEntry] = {}


def issue_token(phone: str) -> str:
    """Mints a fresh token bound to `phone`. Each call is a new,
    independent token — nothing about a previously issued one for the same
    number is reused, extended, or invalidated."""
    token = secrets.token_urlsafe(24)
    with _lock:
        _tokens[token] = _TokenEntry(phone=phone, expires_at=time.monotonic() + _TOKEN_TTL_SECONDS)
    return token


def resolve_token(token: str) -> Optional[str]:
    """Returns the phone number `token` was issued for, or None if it's
    unknown or has expired. Never raises."""
    with _lock:
        entry = _tokens.get(token)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            del _tokens[token]
            return None
        return entry.phone
