"""Every fixed message this feature sends — no LLM call anywhere in this
flow, by design (the trigger is a deterministic reel-link match, not
something that needs language understanding). Kept in one file, separate
from instagram_event_service.py's control flow, so the actual wording is
easy to find and edit without touching the event-handling/matching logic.

build_property_info_message deliberately excludes society_name, address,
contact_name and contact_phone — the whole point of routing an interested
commenter through this flow (rather than just answering with everything)
is to keep the broker in the loop, not hand out enough detail for someone
to find and contact the owner directly.
"""

from __future__ import annotations

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

COMMENT_REPLY_TEXT = "Plzz check your DM! 📩"

# Reply used when the commenter has ALREADY given a WhatsApp number (their
# instagram_contacts row has linked_phone set). Nothing is DMed to them any
# more — every further message goes to WhatsApp — so pointing them at their
# Instagram inbox would be pointing at a message that is never coming.
COMMENT_REPLY_ON_WHATSAPP_TEXT = (
    "Thanks for reaching out! 🙌 Our team will get in touch with you on WhatsApp."
)

# Sent instead of repeating the whole two-message sequence when someone
# comments AGAIN on a property they've already been DMed about. It exists so
# that "Plzz check your DM!" is always backed by a real, new DM landing in
# their inbox — a repeat commenter used to get the reply and nothing else.
DM_REPEAT_NUDGE_TEXT = (
    "Hi again! 👋 Saw your comment — the details for this property are right here in our chat above. "
    "Let us know if you'd like to know anything more 🙂"
)

# The SECOND (and last) message of the sequence: one line asking for their
# requirement, with the personal requirements-form link in that same message.
# The sequence is exactly two messages -- the property details, then this.
DM_REQUIREMENT_LINK_TEMPLATE = "Tell us your requirement by clicking on this link:\n{link}"

# Sent once the requirements form is submitted — mirrors Service/
# WhatsAppInquiryHandlingService/inquiry_form_service.py's _CONFIRMATION_TEXT
# wording for the case where they gave a WhatsApp number (that exact text is
# reused as-is, not duplicated here) — this one is only for the Instagram-
# only path (no phone given).
INSTAGRAM_ONLY_CONFIRMATION_TEXT = (
    "Thank you! We've received your requirements — our team will reach out to you here on Instagram soon."
)

# The Instagram-only twin of inquiry_form_service._FINAL_UPDATE_TEXT, sent
# alongside the confirmation on the LAST update the form will accept (see
# that module's MAX_REQUIREMENT_SUBMISSIONS) so nobody discovers the limit
# only by running into it.
INSTAGRAM_ONLY_FINAL_UPDATE_TEXT = (
    "Thank you! We've received your updated requirements — our team will reach out to you here on "
    "Instagram soon.\n\n"
    "Just to let you know, this was the third and final update we can take through the online form. If "
    "anything changes again, please don't worry — simply message us here and one of our team will be "
    "very happy to update it for you personally."
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


def _area_line(prop: StructuredProperty) -> str | None:
    """The property's size in whichever unit the listing recorded it in —
    both, joined, in the rare case it holds both. Never converted between
    the two: a number only means something next to its own unit."""
    parts = [
        f"{round(value)} {unit}"
        for value, unit in ((prop.area_sqft, "sqft"), (prop.area_vaar, "var"))
        if value is not None
    ]
    return " / ".join(parts) if parts else None


def build_property_info_message(prop: StructuredProperty) -> str:
    """Only locality, price, size, BHK and type — see the module docstring
    for why. Any field that's missing on this particular property is simply
    left out of the list, never shown as a blank/placeholder.

    Note what is NOT here, and must never be added: the address, the contact
    details, and above all location_url (the internal map pin). This message
    goes to a stranger on Instagram — see StructuredProperty.location_url."""
    lines = []
    if prop.area_name:
        lines.append(f"📍 Area: {prop.area_name}")
    price = _price_line(prop)
    if price:
        lines.append(f"💰 Price: {price}")
    area = _area_line(prop)
    if area:
        lines.append(f"📐 Area: {area}")
    if prop.bhk:
        lines.append(f"🛏️ BHK: {prop.bhk}")
    if prop.property_type:
        lines.append(f"🏠 Type: {prop.property_type} ({prop.listing_type})")

    details = "\n".join(lines) if lines else "(details coming up shortly from our team)"
    return f"Hi! Thanks for your interest 😊 Here are the details of this property:\n\n{details}"


def build_requirement_link_message(form_link: str) -> str:
    """Second and last message of the DM sequence -- see
    DM_REQUIREMENT_LINK_TEMPLATE. The link is inside this one message, never
    a message of its own."""
    return DM_REQUIREMENT_LINK_TEMPLATE.format(link=form_link)
