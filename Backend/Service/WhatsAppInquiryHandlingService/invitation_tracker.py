"""Tracks which phone numbers have already been sent the welcome +
registration-form-link message, WITHOUT writing anything to the durable
client database — a client record is only ever created once they actually
submit the form (see inquiry_form_service.submit_form). This is purely an
in-memory guard against re-sending the welcome message on every subsequent
qualifying batch from someone who hasn't filled the form in yet
(duplicate-message prevention).

In-memory only, deliberately: losing this on a restart just means a
returning message might trigger one extra welcome resend — harmless,
unlike losing actual client data.
"""

from __future__ import annotations

import threading
from typing import Set

_lock = threading.Lock()
_invited_phones: Set[str] = set()


def mark_invited(phone: str) -> None:
    with _lock:
        _invited_phones.add(phone)


def forget(phone: str) -> None:
    """Drops the "already invited" mark for one number, so it is treated as
    a first-time texter again. Called only when that inquiry is deleted
    outright (see Controller/WhatsAppInquiryHandlingController/
    whatsapp_inquiry_controller.py's delete_client) — without this, a
    deleted number that messages again would get neither the welcome nor a
    fresh form link, because this tracker still remembered a link it sent
    for a client that no longer exists."""
    with _lock:
        _invited_phones.discard(phone)


def was_invited(phone: str) -> bool:
    with _lock:
        return phone in _invited_phones
