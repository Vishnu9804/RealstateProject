"""Sends a property shortlist to the person who asked for it — and, above
all, decides WHICH of the operator's own linked WhatsApp numbers it goes out
from.

That second part is the whole reason this module exists rather than the
controllers calling outbound_messenger directly. "Reply from the number they
reached us on" is one rule with two lookups behind it, and it has to give
the same answer to the send call and to the dialog that previews it — if the
dialog said one number and the send used another, the operator would have
been shown something untrue about a message that has already left.

THE RULE, for both sides:

  broker requirement -> the connection the requirement was captured on
                        (StructuredRequirement.source_connection_id).
  client inquiry     -> the connection that client's inquiry arrived on
                        (Service/WhatsAppInquiryHandlingService/
                        inquiry_connection_store.py).

...and in either case, when that is unknown or currently offline, the first
listening connection holding the "inquiry" role — which is "the first number
selected for client inquiries on the Connection page", in the order that
page itself lists them (see whatsapp_connection_manager._pick_sender). That
fallback is what covers a website enquiry and an Instagram enquiry, neither
of which ever arrived on a WhatsApp number of ours at all, as well as any
record that predates this feature.

The RECIPIENT is never inferred here. It is the broker's own sender_phone
for a requirement, and the client's own phone for an inquiry — both the
identity those records are keyed by, so a shortlist cannot be sent to
somebody it was not meant for.
"""

from __future__ import annotations

import base64
import binascii
from typing import List, Optional, Sequence, Tuple

from Middleware import step_logger
from Model.PropertySharingModel.share_result import PropertyBatchShareResult, ShareResult, ShareTarget
from Service.BrokerRequirementService import requirement_store
from Service.WhatsAppDataFetchingService import property_vector_store, whatsapp_connection_manager
from Service.WhatsAppInquiryHandlingService import client_store, inquiry_connection_store, outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_PREFER_ROLE = "inquiry"


def get_requirement_target(record_id: str) -> Optional[ShareTarget]:
    """Who a requirement's shortlist would go to, and from which of our
    numbers — read-only, and the exact same resolution
    send_for_requirement performs. None when no such requirement exists."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None
    return ShareTarget(
        to_phone=requirement.sender_phone,
        to_name=requirement.contact_name or requirement.sender_saved_name or requirement.sender_name,
        from_number=whatsapp_connection_manager.get_sender_number(
            prefer_role=_PREFER_ROLE, connection_id=requirement.source_connection_id
        ),
    )


def get_client_target(phone: str) -> Optional[ShareTarget]:
    """Same, for a client inquiry. None when no client record exists for
    this number — including a website/Instagram lead, which DOES have one
    (see Service/LandingPageService/landing_page_service.py's
    _sync_to_inquiries), so in practice this is only None for a number the
    dashboard does not know at all."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None
    return ShareTarget(
        to_phone=client.phone,
        to_name=client.name,
        from_number=whatsapp_connection_manager.get_sender_number(
            prefer_role=_PREFER_ROLE, connection_id=inquiry_connection_store.get(client.phone)
        ),
    )


def send_for_requirement(record_id: str, message: str) -> Optional[ShareResult]:
    """Sends `message` — already rendered AND possibly hand-edited by the
    operator in the send dialog — to the broker behind this requirement.

    The text is passed through verbatim on purpose. The templates
    (Service/PropertySharingService/property_share_template_service.py) are
    the DEFAULT wording; what the operator approved in the dialog is what
    must actually go out, and it is deliberately not written back to the
    stored template."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None
    target = normalize_phone(requirement.sender_phone) or requirement.sender_phone
    from_number = whatsapp_connection_manager.get_sender_number(
        prefer_role=_PREFER_ROLE, connection_id=requirement.source_connection_id
    )
    sent = outbound_messenger.send_text(target, message, connection_id=requirement.source_connection_id)
    _log("requirement", record_id, target, from_number, sent)
    return ShareResult(sent=sent, to_phone=target, from_number=from_number)


def send_for_client(phone: str, message: str) -> Optional[ShareResult]:
    """Sends `message` to a client on the number their record is keyed by —
    same verbatim-text reasoning as send_for_requirement above."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None
    connection_id = inquiry_connection_store.get(client.phone)
    from_number = whatsapp_connection_manager.get_sender_number(
        prefer_role=_PREFER_ROLE, connection_id=connection_id
    )
    sent = outbound_messenger.send_text(client.phone, message, connection_id=connection_id)
    _log("client", client.phone, client.phone, from_number, sent)
    return ShareResult(sent=sent, to_phone=client.phone, from_number=from_number)


# Photos sent per property. A listing can hold many; beyond this the chat
# turns into a photo dump and the send takes minutes.
MAX_PHOTOS_PER_PROPERTY = 10
# WhatsApp's caption limit. Longer details still go out, as a text message
# straight after the photos instead of as their caption.
_MAX_CAPTION_LENGTH = 1024


def send_properties_to_client(
    phone: str,
    intro: str,
    closing: str,
    properties: Sequence[Tuple[str, str]],
) -> Optional[PropertyBatchShareResult]:
    """Sends a client their shortlist as SEPARATE messages, in order:

      1. `intro` (skipped when blank);
      2. per (record_id, details) — its photos, with `details` as the caption
         of the last photo so the details and the photos read as one message;
         just `details` as text when the property has no photos;
      3. `closing` (skipped when blank).

    Text is passed through verbatim, same reasoning as send_for_client. None
    when no client record exists for this number."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None
    connection_id = inquiry_connection_store.get(client.phone)
    from_number = whatsapp_connection_manager.get_sender_number(prefer_role=_PREFER_ROLE, connection_id=connection_id)
    result = PropertyBatchShareResult(sent=False, to_phone=client.phone, from_number=from_number)

    # Nothing connected: every send would fail one by one — report it at once.
    if whatsapp_connection_manager.get_sender_client(prefer_role=_PREFER_ROLE, connection_id=connection_id) is None:
        result.properties_failed = len(properties)
        _log("client", client.phone, client.phone, from_number, False)
        return result

    all_sent = True
    if intro.strip():
        all_sent = outbound_messenger.send_text(client.phone, intro, connection_id=connection_id) and all_sent

    for record_id, details in properties:
        delivered, photos = _send_one_property(client.phone, record_id, details, connection_id)
        result.photos_sent += photos
        if delivered:
            result.properties_sent += 1
        else:
            result.properties_failed += 1
            all_sent = False

    if closing.strip():
        all_sent = outbound_messenger.send_text(client.phone, closing, connection_id=connection_id) and all_sent

    result.sent = all_sent
    _log("client", client.phone, client.phone, from_number, all_sent)
    return result


def _send_one_property(phone: str, record_id: str, details: str, connection_id: Optional[str]) -> Tuple[bool, int]:
    """(details delivered, photos delivered) for one property."""
    photos = _property_photos(record_id)
    if not photos:
        return outbound_messenger.send_text(phone, details, connection_id=connection_id), 0

    caption_fits = len(details) <= _MAX_CAPTION_LENGTH
    photos_sent = 0
    for index, photo in enumerate(photos):
        is_last = index == len(photos) - 1
        caption = details if (is_last and caption_fits) else None
        if outbound_messenger.send_image(phone, photo, caption, connection_id=connection_id):
            photos_sent += 1
            if caption is not None:
                return True, photos_sent
    # Either the details were too long for a caption, or the captioned photo
    # did not go out — the details must still arrive, right after the photos.
    return outbound_messenger.send_text(phone, details, connection_id=connection_id), photos_sent


def _property_photos(record_id: str) -> List[bytes]:
    """The property's photos as raw bytes, at most MAX_PHOTOS_PER_PROPERTY.
    Looks in the property database first, then Builder Projects (the
    matches dialog can list either)."""
    images = property_vector_store.get_property_images(record_id)
    if images is None:
        from Service.BuilderProjectService import builder_project_store

        images = builder_project_store.get_images(record_id)
    photos: List[bytes] = []
    for value in images or []:
        decoded = _decode_data_url(value)
        if decoded:
            photos.append(decoded)
        if len(photos) >= MAX_PHOTOS_PER_PROPERTY:
            break
    return photos


def _decode_data_url(value: str) -> Optional[bytes]:
    """Photos are stored as base64 data URLs (Database/models.py's
    PropertyRow.image_urls). Anything else is skipped rather than failing
    the whole send."""
    if not isinstance(value, str) or not value.startswith("data:"):
        return None
    header, separator, payload = value.partition(",")
    if not separator or ";base64" not in header:
        return None
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError):
        step_logger.warn("[Share] Skipped a property photo that could not be decoded.")
        return None


def _log(kind: str, subject: str, to_phone: str, from_number: Optional[str], sent: bool) -> None:
    origin = f" from {from_number}" if from_number else ""
    if sent:
        step_logger.success(f"[Share] {kind} {subject}: property details sent to {to_phone}{origin}.")
    else:
        step_logger.error(
            f"[Share] {kind} {subject}: FAILED to send property details to {to_phone}{origin} — "
            "no connected number was available."
        )
