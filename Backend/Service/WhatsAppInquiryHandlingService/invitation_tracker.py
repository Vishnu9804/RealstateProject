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
from collections import OrderedDict

# A ceiling, with the oldest mark dropped once it is reached.
#
# This was an unbounded set on a process designed to run for months, fed by
# anyone who can send a WhatsApp message — so it grew with every number
# that ever messaged in, and nothing ever removed an entry except an
# explicit deletion on the Inquiries page. Being bounded costs almost
# nothing here: the ONLY consequence of dropping a mark is that a number
# which messaged months ago, never filled the form in, and messages again
# receives the welcome and the form link a second time. That is a perfectly
# reasonable thing to send someone in that situation, which is why this is
# the safest of the in-memory tables to bound.
#
# Oldest-first, because recency is exactly what matters: the mark that
# stops a duplicate welcome only has to survive as long as the
# conversation it belongs to.
_MAX_TRACKED = 50_000

_lock = threading.Lock()
# An ordered mapping used as an ordered set — the value is never read.
_invited_phones: "OrderedDict[str, None]" = OrderedDict()


def mark_invited(phone: str) -> None:
    with _lock:
        _invited_phones[phone] = None
        _invited_phones.move_to_end(phone)
        while len(_invited_phones) > _MAX_TRACKED:
            _invited_phones.popitem(last=False)


def forget(phone: str) -> None:
    """Drops the "already invited" mark for one number, so it is treated as
    a first-time texter again. Called only when that inquiry is deleted
    outright (see Controller/WhatsAppInquiryHandlingController/
    whatsapp_inquiry_controller.py's delete_client) — without this, a
    deleted number that messages again would get neither the welcome nor a
    fresh form link, because this tracker still remembered a link it sent
    for a client that no longer exists."""
    with _lock:
        _invited_phones.pop(phone, None)


def was_invited(phone: str) -> bool:
    with _lock:
        return phone in _invited_phones
