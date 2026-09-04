"""Handles a registration/update form submission — the backend side of the
link sent by inquiry_pipeline_service.py's WhatsApp messages, or by
Service/InstagramInquiryHandlingService/instagram_polling_service.py's DM
sequence. Turns a submitted set of fields into a durable record and sends
the confirmation message on whichever channel is now the right one for
this person.

Called only from Controller/WhatsAppInquiryHandlingController/
inquiry_form_controller.py, which has already resolved the URL token to a
(channel, identity) pair (see form_token_service.py) before either function
here is called — this module never sees or trusts a client-submitted phone
number for a "whatsapp" token, and for an "instagram" token trusts
FormSubmissionRequest.phone as the one deliberate exception (see that
model's docstring).
"""

from __future__ import annotations

from typing import Optional

from Middleware import step_logger
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.form_submission import Channel, FormPrefillResponse, FormSubmissionRequest
from Service.InstagramInquiryHandlingService import instagram_contact_store, instagram_message_templates, instagram_messenger
from Service.WhatsAppInquiryHandlingService import client_store, outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_CONFIRMATION_TEXT = "We have received your requirements. Our agent will contact you soon."

_REQUIREMENT_FIELDS = (
    "name",
    "email",
    "purpose",
    "property_type",
    "bhk",
    "budget_min_inr",
    "budget_max_inr",
    "preferred_areas",
    "additional_requirements",
)


def get_prefill(channel: Channel, identity: str) -> FormPrefillResponse:
    if channel == "whatsapp":
        record = client_store.get_client_by_phone(identity)
        is_new_client = record is None or record.status == "pending_registration"
        if record is None:
            return FormPrefillResponse(is_new_client=True, channel="whatsapp", phone=identity)
        return FormPrefillResponse(
            is_new_client=is_new_client,
            channel="whatsapp",
            phone=identity,
            **record.model_dump(exclude={"phone", "status", "pending_action", "created_at", "updated_at"}),
        )

    # channel == "instagram"
    contact = instagram_contact_store.get_contact(identity)
    if contact is not None and contact.linked_phone:
        # Already converted to a WhatsApp client on a previous visit —
        # prefill from the real, current ClientRow, not the (now frozen)
        # snapshot left on the Instagram contact row.
        client = client_store.get_client_by_phone(contact.linked_phone)
        if client is not None:
            return FormPrefillResponse(
                is_new_client=False,
                channel="instagram",
                phone=contact.linked_phone,
                **client.model_dump(exclude={"phone", "status", "pending_action", "created_at", "updated_at"}),
            )
    if contact is None:
        return FormPrefillResponse(is_new_client=True, channel="instagram", phone=None)
    return FormPrefillResponse(
        is_new_client=contact.status == "new",
        channel="instagram",
        phone=None,
        **contact.model_dump(exclude={"ig_user_id", "ig_username", "status", "linked_phone", "created_at", "updated_at"}),
    )


def submit_form(channel: Channel, identity: str, submission: FormSubmissionRequest) -> None:
    if channel == "whatsapp":
        _submit_whatsapp(identity, submission)
        return
    _submit_instagram(identity, submission)


def _extract_requirement_fields(submission: FormSubmissionRequest) -> dict:
    """Every field but budget_*_inr is free text, where a blank string
    means "clear this field" (see FormSubmissionRequest's docstring), so it
    gets normalized to None; the two budget fields are already numbers
    (or None) with no such distinction to make."""
    fields = {field: getattr(submission, field) for field in _REQUIREMENT_FIELDS}
    for field in fields:
        if field not in ("budget_min_inr", "budget_max_inr"):
            fields[field] = _blank_to_none(fields[field])
    return fields


def _submit_whatsapp(phone: str, submission: FormSubmissionRequest) -> None:
    """Byte-for-byte the pre-existing behavior — submission.phone is never
    read here, so a whatsapp-channel token's identity can't be overridden
    by anything in the request body."""
    record = ClientRecord(
        phone=phone,
        status="registered",
        pending_action=None,
        **_extract_requirement_fields(submission),
    )
    client_store.upsert_client(record)

    sent = outbound_messenger.send_text(phone, _CONFIRMATION_TEXT)
    if sent:
        step_logger.success(f"[Inquiry] {phone}: form submitted, confirmation message sent.")
    else:
        step_logger.error(f"[Inquiry] {phone}: form submitted (saved OK) but FAILED to send confirmation message.")


def _submit_instagram(ig_user_id: str, submission: FormSubmissionRequest) -> None:
    existing_contact = instagram_contact_store.get_contact(ig_user_id)
    ig_username = existing_contact.ig_username if existing_contact is not None else None
    normalized_phone = normalize_phone(submission.phone) if submission.phone else None
    requirement_fields = _extract_requirement_fields(submission)

    if normalized_phone:
        # Converts to a real WhatsApp client — unified into the same
        # Inquiries dashboard as any WhatsApp-originated one, and every
        # future message to this person goes to WhatsApp, never Instagram
        # DM again (instagram_polling_service checks linked_phone).
        client_record = ClientRecord(phone=normalized_phone, status="registered", pending_action=None, **requirement_fields)
        client_store.upsert_client(client_record)

        contact_record = InstagramContactRecord(
            ig_user_id=ig_user_id,
            ig_username=ig_username,
            status="converted",
            linked_phone=normalized_phone,
            **requirement_fields,
        )
        instagram_contact_store.upsert_contact(contact_record)

        sent = outbound_messenger.send_text(normalized_phone, _CONFIRMATION_TEXT)
        if sent:
            step_logger.success(
                f"[Inquiry] Instagram user {ig_user_id!r} submitted with WhatsApp number {normalized_phone!r} — "
                "converted to a WhatsApp client, confirmation sent there."
            )
        else:
            step_logger.error(
                f"[Inquiry] Instagram user {ig_user_id!r} converted to WhatsApp client {normalized_phone!r} "
                "(saved OK) but FAILED to send the WhatsApp confirmation message."
            )
        return

    contact_record = InstagramContactRecord(
        ig_user_id=ig_user_id, ig_username=ig_username, status="registered", **requirement_fields
    )
    instagram_contact_store.upsert_contact(contact_record)

    sent = instagram_messenger.send_dm_to_user(ig_user_id, instagram_message_templates.INSTAGRAM_ONLY_CONFIRMATION_TEXT)
    if sent:
        step_logger.success(f"[Inquiry] Instagram user {ig_user_id!r} submitted (Instagram-only), confirmation DM sent.")
    else:
        step_logger.error(
            f"[Inquiry] Instagram user {ig_user_id!r} submitted (saved OK) but FAILED to send the confirmation DM."
        )


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
