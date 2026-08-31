"""Normalizes a phone number to one canonical E.164 form (e.g.
"+919876543210") so the same person is never split across two different
client records because WhatsApp and a manually-typed form spelled their
number differently (with/without a country code, spaces, dashes, etc.).

This is the one thing every client lookup/write must go through — see
Service/WhatsAppInquiryHandlingService/client_store.py and
inquiry_pipeline_service.py. Getting this wrong is exactly the kind of bug
the feature spec calls out as business-critical: two records for one real
client means their requirements/replies can silently go to (or be shown
as) the wrong conversation.
"""

from __future__ import annotations

from typing import Optional

import phonenumbers

# WhatsApp-derived numbers already arrive with a leading "+" (see
# WhatsAppInquiryClient._resolve_sender_phone), but a form-submitted number
# often won't — default to India so a bare 10-digit number still parses
# correctly. This feature only ever deals with Indian real estate inquiries.
_DEFAULT_REGION = "IN"


def normalize_phone(raw: str) -> Optional[str]:
    """Returns the E.164 form of `raw` (e.g. "+919876543210"), or None if
    it isn't a parseable/valid phone number at all."""
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, _DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
