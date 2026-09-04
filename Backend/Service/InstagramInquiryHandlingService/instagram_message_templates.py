"""Every fixed message this feature sends — no LLM call anywhere in this
flow, by design (the trigger is a deterministic reel-link match, not
something that needs language understanding). Kept in one file, separate
from instagram_polling_service.py's control flow, so the actual wording is
easy to find and edit without touching the polling/matching logic.

build_property_info_message deliberately excludes society_name, address,
contact_name and contact_phone — the whole point of routing an interested
commenter through this flow (rather than just answering with everything)
is to keep the broker in the loop, not hand out enough detail for someone
to find and contact the owner directly.
"""

from __future__ import annotations

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

COMMENT_REPLY_TEXT = "Plzz check your DM! 📩"

DM_FOLLOWUP_TEXT = "Does this match what you're looking for? 🙂"

DM_MORE_OPTIONS_TEMPLATE = (
    "We also have plenty of other options available! Just click the link below and fill a quick form with your "
    "requirements, and we'll help you find the perfect match:\n{link}"
)

# Sent once the requirements form is submitted — mirrors Service/
# WhatsAppInquiryHandlingService/inquiry_form_service.py's _CONFIRMATION_TEXT
# wording for the case where they gave a WhatsApp number (that exact text is
# reused as-is, not duplicated here) — this one is only for the Instagram-
# only path (no phone given).
INSTAGRAM_ONLY_CONFIRMATION_TEXT = (
    "Thank you! We've received your requirements — our team will reach out to you here on Instagram soon."
)

_CRORE = 10_000_000
_LAKH = 100_000
_THOUSAND = 1_000


def _format_compact_inr(amount: float) -> str:
    magnitude = abs(amount)
    if magnitude >= _CRORE:
        return f"{_trim(amount / _CRORE)}cr"
    if magnitude >= _LAKH:
        return f"{_trim(amount / _LAKH)}L"
    if magnitude >= _THOUSAND:
        return f"{_trim(amount / _THOUSAND)}k"
    return _trim(amount)


def _trim(value: float) -> str:
    rounded = round(value, 2)
    return f"{rounded:g}"


def _price_line(prop: StructuredProperty) -> str | None:
    if prop.price_text:
        return prop.price_text
    if prop.price_amount_inr is not None:
        return _format_compact_inr(prop.price_amount_inr)
    return None


def _carpet_area_line(prop: StructuredProperty) -> str | None:
    if prop.carpet_area_sqft is None:
        return None
    unit = prop.carpet_area_unit or "sqft"
    return f"{round(prop.carpet_area_sqft)} {unit}"


def build_property_info_message(prop: StructuredProperty) -> str:
    """Only area, price, carpet area, BHK and type — see the module
    docstring for why. Any field that's missing on this particular property
    is simply left out of the list, never shown as a blank/placeholder."""
    lines = []
    if prop.area_name:
        lines.append(f"📍 Area: {prop.area_name}")
    price = _price_line(prop)
    if price:
        lines.append(f"💰 Price: {price}")
    carpet_area = _carpet_area_line(prop)
    if carpet_area:
        lines.append(f"📐 Carpet area: {carpet_area}")
    if prop.bhk:
        lines.append(f"🛏️ BHK: {prop.bhk}")
    if prop.property_type:
        lines.append(f"🏠 Type: {prop.property_type} ({prop.listing_type})")

    details = "\n".join(lines) if lines else "(details coming up shortly from our team)"
    return f"Hi! Thanks for your interest 😊 Here are the details of this property:\n\n{details}"


def build_more_options_message(form_link: str) -> str:
    return DM_MORE_OPTIONS_TEMPLATE.format(link=form_link)
