"""Remembers which of our linked WhatsApp numbers each client's inquiry
actually arrived on, so a later outbound message to that client can go out
from the SAME number rather than from whichever connection happens to be
listening.

WHY THIS IS ITS OWN STORE, AND NOT A COLUMN ON `clients`

Every write into the client table goes through client_store.upsert_client,
which rewrites the whole record from whatever ClientRecord it is handed —
and several callers legitimately build a fresh one (a form submission, a
website enquiry, an Instagram conversion; see
Service/LandingPageService/landing_page_service.py's _sync_to_inquiries and
Service/WhatsAppInquiryHandlingService/inquiry_form_service.py). A column
there would therefore be silently blanked by the next save that didn't know
to carry it, which is exactly the kind of quiet data loss that is
impossible to notice from the UI. Keeping it here means the one thing that
writes it is the one thing that knows it.

It is also genuinely not client DATA: it is a routing fact about a
conversation, in the same family as monitoring_selection_store.py and
whatsapp_connections_store.py — both of which persist through the same
app_settings key/value layer this does.

WHAT HAPPENS WITHOUT IT

Nothing breaks. A phone with no entry (a website lead, an Instagram
conversion, a client who messaged before this existed, or a fresh process
with no database) resolves to "no preference", and sending falls back to
the first listening connection holding the inquiry role — which is the
behaviour every outbound message had before this module existed, and
exactly what the product asks for in those cases.
"""

from __future__ import annotations

from typing import Dict, Optional

from Database import settings_repository
from Database.session import is_database_configured
from Middleware import step_logger

_SETTINGS_KEY = "inquiry_source_connections"

# Bounded so a long-running install can never grow this without limit. The
# oldest entries go first; losing one only means that client's next outbound
# message falls back to the default connection, never an error.
_MAX_ENTRIES = 5000

# phone (E.164) -> connection_id. Authoritative in memory; written through
# to app_settings so it survives a restart, exactly like every other
# *_selection/*_settings store here.
_by_phone: Dict[str, str] = {}


def load_from_database() -> None:
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if not isinstance(stored, dict):
        return
    mapping = stored.get("by_phone")
    if isinstance(mapping, dict):
        _by_phone.clear()
        _by_phone.update({str(k): str(v) for k, v in mapping.items() if k and v})


def remember(phone: str, connection_id: Optional[str]) -> None:
    """Records that `phone`'s inquiry came in on `connection_id`. A no-op
    when nothing changed, which is the overwhelmingly common case (the same
    client keeps messaging the same number) — so the database is only
    touched when the answer is actually new, not once per message.

    Never raises: this sits on the inquiry pipeline's hot path, and losing a
    routing hint must never cost an actual inquiry."""
    if not phone or not connection_id:
        return
    if _by_phone.get(phone) == connection_id:
        return
    _by_phone[phone] = connection_id
    if len(_by_phone) > _MAX_ENTRIES:
        # dicts preserve insertion order, so this drops the least recently
        # (re)assigned entries first.
        for stale in list(_by_phone)[: len(_by_phone) - _MAX_ENTRIES]:
            _by_phone.pop(stale, None)
    if not is_database_configured():
        return
    try:
        settings_repository.set_value(_SETTINGS_KEY, {"by_phone": dict(_by_phone)})
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"[Inquiry] Could not persist the source connection for {phone}: {exc!r}")


def get(phone: str) -> Optional[str]:
    """Which connection this client's inquiry arrived on, or None for
    "no preference" — a website/Instagram lead, or a client from before this
    was recorded. See the module docstring for what None means downstream."""
    return _by_phone.get(phone)


def forget(phone: str) -> None:
    """Dropped alongside the client record itself, so a deleted number
    leaves nothing behind (see whatsapp_inquiry_controller.delete_client)."""
    if _by_phone.pop(phone, None) is None:
        return
    if not is_database_configured():
        return
    try:
        settings_repository.set_value(_SETTINGS_KEY, {"by_phone": dict(_by_phone)})
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"[Inquiry] Could not persist the source-connection removal for {phone}: {exc!r}")
