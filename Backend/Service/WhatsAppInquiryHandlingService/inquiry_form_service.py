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

import threading
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

# How many times this form may be completed for one identity, in total: the
# first is the original registration, so this allows that plus THREE
# updates.
#
# Why there is a cap at all: this form is open to the public, and every
# accepted submission writes a client row, re-embeds the requirements and
# rewrites that client's entire cached match table. Left unbounded, anyone
# could sit on the page pressing Save and turn a real client's record into
# an unmetered workload on a metered database. Three updates is far more
# than a genuine client has ever needed — they are updating what they want
# from a home, not editing a document — so the guard is invisible to real
# use and decisive against the other kind.
#
# The count is per IDENTITY (phone number, or Instagram account), not per
# browser or per session, because that is the thing being protected. A
# client who genuinely needs a fourth change is not turned away — they are
# asked to reach a person, who can make it for them.
MAX_REQUIREMENT_SUBMISSIONS = 4

# Sent WITH the confirmation on the last update we accept, so nobody
# discovers the limit only by hitting it. Deliberately warm and apologetic
# in tone: from the client's side this is us telling them they have been
# thorough, not telling them off.
_FINAL_UPDATE_TEXT = (
    "We have received your updated requirements. Our agent will contact you soon.\n\n"
    "Just to let you know — this was the third and final update we can take through the online form. "
    "If anything changes again, please don't worry at all: simply reply here on WhatsApp or give us a "
    "call, and one of our team will be very happy to update your requirements for you personally."
)

# What the PAGE says when a further update is refused. There is deliberately
# no WhatsApp message to go with it: a refusal that messaged the client
# every time would turn this endpoint into a way of sending somebody
# unlimited WhatsApp messages, which is the very thing the cap exists to
# prevent. The refusal is silent on WhatsApp and explicit on screen.
LIMIT_REACHED_NOTICE = (
    "You've already updated your requirements three times, so we've kept them exactly as they are for "
    "now. Nothing is lost — just reply to us on WhatsApp or give us a call, and one of our team will "
    "gladly make any further changes for you."
)

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
            return FormPrefillResponse(
                is_new_client=True,
                channel="whatsapp",
                phone=identity,
                updates_remaining=MAX_REQUIREMENT_SUBMISSIONS,
            )
        return FormPrefillResponse(
            is_new_client=is_new_client,
            channel="whatsapp",
            phone=identity,
            has_active_assignment=assignment_lock_service.has_active_assignment(identity),
            updates_remaining=_updates_remaining(record.requirement_submission_count),
            **record.model_dump(exclude=_PREFILL_EXCLUDE),
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
                updates_remaining=_updates_remaining(client.requirement_submission_count),
                **client.model_dump(exclude=_PREFILL_EXCLUDE),
            )
    if contact is None:
        return FormPrefillResponse(
            is_new_client=True,
            channel="instagram",
            phone=None,
            updates_remaining=MAX_REQUIREMENT_SUBMISSIONS,
        )
    return FormPrefillResponse(
        is_new_client=contact.status == "new",
        channel="instagram",
        phone=None,
        updates_remaining=_updates_remaining(contact.requirement_submission_count),
        **contact.model_dump(
            exclude={
                "ig_user_id",
                "ig_username",
                "status",
                "linked_phone",
                "requirement_submission_count",
                "created_at",
                "updated_at",
            }
        ),
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
        return FormPrefillResponse(
            is_new_client=True, channel="whatsapp", phone=phone, updates_remaining=MAX_REQUIREMENT_SUBMISSIONS
        )
    return FormPrefillResponse(
        is_new_client=record.status in ("pending_registration", "website_lead"),
        channel="whatsapp",
        phone=phone,
        has_active_assignment=assignment_lock_service.has_active_assignment(phone),
        updates_remaining=_updates_remaining(record.requirement_submission_count),
        **record.model_dump(exclude=_PREFILL_EXCLUDE),
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


# Everything a stored record carries that the form has no business
# echoing back to a browser. Named once so the four prefill paths above
# cannot drift apart — the mistake that list is here to prevent is adding a
# column and quietly publishing it on a public endpoint.
_PREFILL_EXCLUDE = {
    "phone",
    "status",
    "pending_action",
    "requirement_submission_count",
    "assigned_agent_id",
    "handoff_sent_at",
    "created_at",
    "updated_at",
}


def _updates_remaining(used: int) -> int:
    """How many more times this identity may submit — 0 meaning the form
    will refuse the next one. A warning the page can show BEFORE someone
    retypes everything, exactly like has_active_assignment; the decision
    itself is still made server-side on submit and never here."""
    return max(0, MAX_REQUIREMENT_SUBMISSIONS - used)


def _send_in_background(send, description: str) -> None:
    """Runs one outbound message on a daemon thread.

    Every send in this module is a WhatsApp (or Instagram) round trip made
    while a visitor watches a spinner on a public page, and not one of them
    is something the page's answer depends on — the submission is already
    stored by the time any of them is called. Sending inline made the button
    sit there spinning for the length of somebody else's network call. Same
    pattern, and the same reasoning, as otp_service.request_otp.

    The thread swallows nothing: each send logs its own success or failure
    exactly as it did before, it just does so a moment after the visitor has
    already been told "saved"."""

    def run() -> None:
        try:
            send()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"[Inquiry] {description} failed: {exc!r}")

    threading.Thread(target=run, name="inquiry-outbound", daemon=True).start()


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

    Three ways this ends, and only the first writes anything:

      "ok"            — saved. The confirmation goes out on a background
                        thread (see _send_in_background): the visitor is
                        watching a spinner, and a WhatsApp round trip is not
                        something their answer depends on.
      "locked"        — a site visit is already assigned to an agent, so the
                        requirements they were briefed on must not change
                        underneath them. See assignment_lock_service.py.
      "limit_reached" — this identity has used its MAX_REQUIREMENT_SUBMISSIONS
                        allowance. Nothing is written and, deliberately,
                        nothing is sent.

    Both refusals leave the record exactly as it was — not the requirements,
    not the status, not the count.
    """
    # Read ONCE, and reused for all three of: the locked notice's summary,
    # the submission count, and upsert_client's "what changed?" comparison.
    # Each of those used to fetch the same row for itself.
    existing = client_store.get_client_by_phone(phone)

    if assignment_lock_service.has_active_assignment(phone):
        _send_in_background(
            lambda: assignment_lock_service.send_locked_notice(phone, existing),
            f"locked-requirements notice to {phone}",
        )
        return FormSubmissionResult(status="locked", message=assignment_lock_service.LOCKED_NOTICE)

    used = existing.requirement_submission_count if existing is not None else 0
    if used >= MAX_REQUIREMENT_SUBMISSIONS:
        step_logger.info(
            f"[Inquiry] {phone}: requirements form submitted again after {used} submissions — refused "
            "(allowance used up), nothing written and no message sent."
        )
        return FormSubmissionResult(status="limit_reached", message=LIMIT_REACHED_NOTICE)

    submission_number = used + 1
    record = ClientRecord(
        phone=phone,
        status="registered",
        pending_action=None,
        requirement_submission_count=submission_number,
        **_extract_requirement_fields(submission),
    )
    # defer_recompute: the match recompute is the slow half of this call and
    # produces nothing the browser is waiting for — see client_store.
    client_store.upsert_client(record, previous=existing, defer_recompute=True)

    is_final = submission_number >= MAX_REQUIREMENT_SUBMISSIONS
    text = _FINAL_UPDATE_TEXT if is_final else _CONFIRMATION_TEXT
    _send_in_background(
        lambda: _log_confirmation(phone, outbound_messenger.send_text(phone, text)),
        f"confirmation message to {phone}",
    )
    step_logger.success(
        f"[Inquiry] {phone}: form submitted (submission {submission_number} of "
        f"{MAX_REQUIREMENT_SUBMISSIONS}{', final update' if is_final else ''}) — saved."
    )
    return FormSubmissionResult(status="ok")


def _log_confirmation(phone: str, sent: bool) -> None:
    """Kept separate only so the background send above stays a one-liner —
    the two log lines are word-for-word the ones this path always wrote."""
    if sent:
        step_logger.success(f"[Inquiry] {phone}: confirmation message sent.")
    else:
        step_logger.error(f"[Inquiry] {phone}: form saved OK but FAILED to send the confirmation message.")


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

    existing_client = client_store.get_client_by_phone(normalized_phone) if normalized_phone else None

    # Same freeze as the WhatsApp path, and it has to be checked here too:
    # an Instagram visitor who gives a number that already has a site visit
    # out with an agent is the same person in the same situation, arriving
    # through a different door. Nothing is written — including the Instagram
    # contact row — so the two stores can't drift apart over a refusal.
    if normalized_phone and assignment_lock_service.has_active_assignment(normalized_phone):
        _send_in_background(
            lambda: assignment_lock_service.send_locked_notice(normalized_phone, existing_client),
            f"locked-requirements notice to {normalized_phone}",
        )
        return FormSubmissionResult(status="locked", message=assignment_lock_service.LOCKED_NOTICE)

    # The allowance follows the IDENTITY, and for someone who has given a
    # WhatsApp number that identity is the phone — so a visitor cannot get a
    # second allowance simply by coming back through their Instagram link.
    # The higher of the two counts wins for the same reason, so neither row
    # falling behind the other can hand out extra updates.
    used = max(
        existing_contact.requirement_submission_count if existing_contact is not None else 0,
        existing_client.requirement_submission_count if existing_client is not None else 0,
    )
    if used >= MAX_REQUIREMENT_SUBMISSIONS:
        step_logger.info(
            f"[Inquiry] Instagram user {ig_user_id!r}: requirements form submitted again after {used} "
            "submissions — refused (allowance used up), nothing written and no message sent."
        )
        return FormSubmissionResult(status="limit_reached", message=LIMIT_REACHED_NOTICE)

    submission_number = used + 1
    is_final = submission_number >= MAX_REQUIREMENT_SUBMISSIONS

    if normalized_phone:
        # Converts to a real WhatsApp client — unified into the same
        # Inquiries dashboard as any WhatsApp-originated one, and every
        # future message to this person goes to WhatsApp, never Instagram
        # DM again (instagram_polling_service checks linked_phone).
        client_record = ClientRecord(
            phone=normalized_phone,
            status="registered",
            pending_action=None,
            requirement_submission_count=submission_number,
            **requirement_fields,
        )
        client_store.upsert_client(client_record, previous=existing_client, defer_recompute=True)

        contact_record = InstagramContactRecord(
            ig_user_id=ig_user_id,
            ig_username=ig_username,
            status="converted",
            linked_phone=normalized_phone,
            requirement_submission_count=submission_number,
            **requirement_fields,
        )
        instagram_contact_store.upsert_contact(contact_record)

        text = _FINAL_UPDATE_TEXT if is_final else _CONFIRMATION_TEXT
        _send_in_background(
            lambda: _log_confirmation(normalized_phone, outbound_messenger.send_text(normalized_phone, text)),
            f"confirmation message to {normalized_phone}",
        )
        step_logger.success(
            f"[Inquiry] Instagram user {ig_user_id!r} submitted with WhatsApp number {normalized_phone!r} — "
            f"converted to a WhatsApp client (submission {submission_number} of {MAX_REQUIREMENT_SUBMISSIONS})."
        )
        return FormSubmissionResult(status="ok")

    contact_record = InstagramContactRecord(
        ig_user_id=ig_user_id,
        ig_username=ig_username,
        status="registered",
        requirement_submission_count=submission_number,
        **requirement_fields,
    )
    instagram_contact_store.upsert_contact(contact_record)

    dm_text = (
        instagram_message_templates.INSTAGRAM_ONLY_CONFIRMATION_TEXT
        if not is_final
        else instagram_message_templates.INSTAGRAM_ONLY_FINAL_UPDATE_TEXT
    )
    _send_in_background(
        lambda: _log_instagram_confirmation(ig_user_id, instagram_messenger.send_dm_to_user(ig_user_id, dm_text)),
        f"confirmation DM to Instagram user {ig_user_id}",
    )
    step_logger.success(
        f"[Inquiry] Instagram user {ig_user_id!r} submitted (Instagram-only, submission "
        f"{submission_number} of {MAX_REQUIREMENT_SUBMISSIONS})."
    )
    return FormSubmissionResult(status="ok")


def _log_instagram_confirmation(ig_user_id: str, sent: bool) -> None:
    if sent:
        step_logger.success(f"[Inquiry] Instagram user {ig_user_id!r}: confirmation DM sent.")
    else:
        step_logger.error(f"[Inquiry] Instagram user {ig_user_id!r}: saved OK but FAILED to send the confirmation DM.")


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
