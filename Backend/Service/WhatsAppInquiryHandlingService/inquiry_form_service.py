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
from Model.WhatsAppInquiryHandlingModel.form_submission import (
    Channel,
    FormPrefillResponse,
    FormSubmissionRequest,
    FormSubmissionResult,
)
from Service.InstagramInquiryHandlingService import instagram_contact_store, instagram_message_templates, instagram_messenger
from Service.WhatsAppInquiryHandlingService import assignment_lock_service, client_store, otp_service, outbound_messenger
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
        # "website_lead" (Service/LandingPageService/landing_page_service.py's
        # _sync_to_inquiries) means a landing-site enquiry created this row
        # from a property's own fields — real as far as the Inquiries page
        # is concerned, but this phone has never actually completed THIS
        # form. It reads as new here for exactly the same reason
        # inquiry_pipeline_service.py treats it as no record at all.
        is_new_client = record is None or record.status in ("pending_registration", "website_lead")
        if record is None:
            return FormPrefillResponse(is_new_client=True, channel="whatsapp", phone=identity)
        return FormPrefillResponse(
            is_new_client=is_new_client,
            channel="whatsapp",
            phone=identity,
            has_active_assignment=assignment_lock_service.has_active_assignment(identity),
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
                has_active_assignment=assignment_lock_service.has_active_assignment(contact.linked_phone),
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


def get_verified_prefill(verification_token: str) -> Optional[FormPrefillResponse]:
    """The public site's own prefill — the one thing the tokenless form
    deliberately did NOT have until now.

    inquiry_form_api.ts used to spell out why there was no public prefill:
    "looking someone up by a number typed into a public form would hand
    their saved requirements to anyone who guessed it". That is still
    exactly right, and it is the reason this takes a VERIFICATION token
    rather than a phone number. The number here is one otp_service.py
    already proved this browser controls, so this is the same trust level
    as a minted form link, reached a different way — never a lookup by a
    number someone typed.

    Returns None when the token is unknown or expired, which the caller
    turns into a 401 and the site treats as "not verified any more".
    """
    phone = otp_service.resolve_verification(verification_token)
    if phone is None:
        return None

    record = client_store.get_client_by_phone(phone)
    # Same "website_lead is not a real registration" reading as the
    # whatsapp branch of get_prefill above — see its comment.
    if record is None:
        return FormPrefillResponse(is_new_client=True, channel="whatsapp", phone=phone)
    return FormPrefillResponse(
        is_new_client=record.status in ("pending_registration", "website_lead"),
        channel="whatsapp",
        phone=phone,
        has_active_assignment=assignment_lock_service.has_active_assignment(phone),
        **record.model_dump(exclude={"phone", "status", "pending_action", "created_at", "updated_at"}),
    )


def submit_form(channel: Channel, identity: str, submission: FormSubmissionRequest) -> FormSubmissionResult:
    if channel == "whatsapp":
        return _submit_whatsapp(identity, submission)
    return _submit_instagram(identity, submission)


def submit_public_form(submission: FormSubmissionRequest) -> Optional[FormSubmissionResult]:
    """The one submission path with NO form token behind it: someone who
    found the requirements form on the public landing site (or an Instagram
    bio link) and gave us their own WhatsApp number, rather than opening a
    link we minted for them.

    The token rule above still holds for every tokened submission — this is
    a deliberately separate door. What comes through it now, though, is a
    number this browser has PROVEN it controls: `verification_token` is
    resolved first (otp_service.py) and, when it resolves, it is the
    identity — `submission.phone` is not even looked at. That closes the
    hole this door used to leave open: previously the only identity here was
    a typed number, so a visitor could attribute their requirements to
    somebody else's phone simply by typing it.

    A typed number with no verification token is still accepted, and that
    is deliberate rather than an oversight: when no WhatsApp connection is
    linked we cannot send a code at all (otp_service's "unavailable"), and
    silently dropping a real enquiry because our own sending number is
    offline is worse than accepting it exactly as this endpoint always did.

    Returns None when there is no usable identity at all, which the
    controller turns into a 400. Everything after that is byte-for-byte the
    normal WhatsApp path, so a landing-page registration is
    indistinguishable from one that came in through a WhatsApp link (same
    ClientRecord, same confirmation message)."""
    phone = otp_service.resolve_verification(submission.verification_token)
    if phone is None:
        phone = normalize_phone(submission.phone or "")
    if phone is None:
        return None
    return _submit_whatsapp(phone, submission)


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


def _submit_whatsapp(phone: str, submission: FormSubmissionRequest) -> FormSubmissionResult:
    """`phone` is always an identity the CALLER established (a form token's,
    or one otp_service proved) — submission.phone is never read here, so
    nothing in the request body can redirect a save at somebody else.

    The one behaviour change since this was "byte-for-byte the pre-existing
    behavior": a client with a site visit already assigned to an agent is
    refused and told why on WhatsApp, instead of having the change land
    silently behind an agent who was briefed on the old requirements. See
    assignment_lock_service.py. Nothing is written in that case — not the
    requirements, not the status — so a refused submission leaves the record
    exactly as the agent was briefed on it."""
    if assignment_lock_service.has_active_assignment(phone):
        assignment_lock_service.send_locked_notice(phone, client_store.get_client_by_phone(phone))
        return FormSubmissionResult(status="locked", message=assignment_lock_service.LOCKED_NOTICE)

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
    return FormSubmissionResult(status="ok")


def _submit_instagram(ig_user_id: str, submission: FormSubmissionRequest) -> FormSubmissionResult:
    existing_contact = instagram_contact_store.get_contact(ig_user_id)
    ig_username = existing_contact.ig_username if existing_contact is not None else None
    # An Instagram visitor's WhatsApp number is the one thing on this form
    # that isn't fixed by the token, so it goes through the same proof the
    # public form does when there is one: a verified number wins over a
    # typed one. Falls back to the typed number unchanged when the visitor
    # was never asked for a code (see otp_service's "unavailable").
    normalized_phone = otp_service.resolve_verification(submission.verification_token)
    if normalized_phone is None and submission.phone:
        normalized_phone = normalize_phone(submission.phone)
    requirement_fields = _extract_requirement_fields(submission)

    # Same freeze as the WhatsApp path, and it has to be checked here too:
    # an Instagram visitor who gives a number that already has a site visit
    # out with an agent is the same person in the same situation, arriving
    # through a different door. Nothing is written — including the Instagram
    # contact row — so the two stores can't drift apart over a refusal.
    if normalized_phone and assignment_lock_service.has_active_assignment(normalized_phone):
        assignment_lock_service.send_locked_notice(normalized_phone, client_store.get_client_by_phone(normalized_phone))
        return FormSubmissionResult(status="locked", message=assignment_lock_service.LOCKED_NOTICE)

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
        return FormSubmissionResult(status="ok")

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
    return FormSubmissionResult(status="ok")


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
