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

from Config.settings import get_settings
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

COMMENT_REPLY_TEXT = "Plzz check your DM! 📩"

# Reply used when the commenter has ALREADY given a WhatsApp number (their
# instagram_contacts row has linked_phone set). Nothing is DMed to them any
# more — every further message goes to WhatsApp — so pointing them at their
# Instagram inbox would be pointing at a message that is never coming.
COMMENT_REPLY_ON_WHATSAPP_TEXT = (
    "Thanks for reaching out! 🙌 Our team will get in touch with you on WhatsApp."
)

# Sent instead of repeating the whole three-message sequence when someone
# comments AGAIN on a property they've already been DMed about. It exists so
# that "Plzz check your DM!" is always backed by a real, new DM landing in
# their inbox — a repeat commenter used to get the reply and nothing else.
DM_REPEAT_NUDGE_TEXT = (
    "Hi again! 👋 Saw your comment — the details for this property are right here in our chat above. "
    "Let us know if you'd like to know anything more 🙂"
)

# The SECOND message of the sequence -- what someone who likes this exact
# property should do next. It used to be a bare "does this match what
# you're looking for?", which asked a question nobody at this stage can
# answer usefully and gave them nothing to act on. A site visit is the real
# next step in this business, and a phone call is the fastest way to book
# one, so the message now names both.
#
# The number comes from Config/settings.py's business_contact_phone and is
# never invented: with nothing configured there the no-number variant goes
# out instead, which still offers the visit but points them back at this
# chat rather than at a number that does not exist.
DM_SITE_VISIT_TEMPLATE = (
    "If you're interested in this property, call us on {phone} and we'll book an offline site visit for "
    "you right away \u2014 come and see it in person at a time that suits you."
)

DM_SITE_VISIT_NO_NUMBER_TEXT = (
    "If you're interested in this property, just reply here and we'll book an offline site visit for you "
    "right away \u2014 come and see it in person at a time that suits you."
)

DM_MORE_OPTIONS_TEMPLATE = (
    "And if you'd like to see more options, just tell us what you're looking for \u2014 click the link "
    "below, fill in your requirements in a minute, and we'll send you the properties that actually "
    "match:\n{link}"
)

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


def build_site_visit_message() -> str:
    """Second message of the DM sequence -- see DM_SITE_VISIT_TEMPLATE.

    Reads the number at call time rather than caching it at import, and
    degrades to the no-number wording when it is blank, so a missing
    setting can never produce a message telling someone to ring nothing."""
    phone = get_settings().business_contact_phone.strip()
    if not phone:
        return DM_SITE_VISIT_NO_NUMBER_TEXT
    return DM_SITE_VISIT_TEMPLATE.format(phone=phone)


def build_more_options_message(form_link: str) -> str:
    return DM_MORE_OPTIONS_TEMPLATE.format(link=form_link)
