from typing import Literal, Optional

from pydantic import BaseModel

Channel = Literal["whatsapp", "instagram"]


class FormSubmissionRequest(BaseModel):
    """Body of a registration/update form submission — see
    Controller/WhatsAppInquiryHandlingController/inquiry_form_controller.py
    and Service/WhatsAppInquiryHandlingService/inquiry_form_service.py.

    Every field is optional: the client's IDENTITY (which phone number or
    Instagram account this is) always comes from the URL token, never from
    this body — so nothing a client submits can ever attribute data to the
    wrong identity. An omitted/blank field is how a client clears something
    they'd previously filled in, not an error.

    `phone` is the one deliberate exception, and only a partial one: it is
    read ONLY when the token's channel is "instagram" (an Instagram visitor
    optionally adding a WhatsApp number is genuinely new information — see
    inquiry_form_service.submit_form) and is silently ignored for a
    "whatsapp" token, whose phone is fixed by the token and never editable,
    exactly as before this field existed.

    `verification_token` is read ONLY by the tokenless "/public" route (see
    inquiry_form_service.submit_public_form) and ignored everywhere else.
    It is the same kind of thing a URL token is — an identity we minted, not
    one the browser asserted — except it was earned by answering a 4-digit
    code we sent to that number (Service/WhatsAppInquiryHandlingService/
    otp_service.py). Where it is present it OUTRANKS `phone`, because a
    proven number beats a typed one."""

    phone: Optional[str] = None
    verification_token: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None


class FormPrefillResponse(BaseModel):
    """What the form page reads before rendering — `is_new_client` tells it
    whether to show a fresh registration form or a pre-filled update form,
    `channel` tells it whether the phone field should render locked
    (whatsapp) or open or optional (instagram). `phone` IS echoed back here
    (unlike before this field existed) specifically so the whatsapp case can
    render it read-only without the page needing to know the number any
    other way; it's still never trusted back from the submission body for
    that channel (see FormSubmissionRequest).

    `has_active_assignment` is a warning, not a permission: it lets the
    page say up front that requirements can't be changed online right now
    (see Service/WhatsAppInquiryHandlingService/assignment_lock_service.py)
    instead of letting someone retype everything and only then be refused.
    The refusal itself is enforced on submit, server-side, never here.

    `updates_remaining` is the same kind of thing: how many more times
    this identity may submit before the form starts refusing (see
    inquiry_form_service.MAX_REQUIREMENT_SUBMISSIONS). It lets the page warn
    someone on their last update BEFORE they retype everything, rather than
    after. Like has_active_assignment it is advisory only — the count is
    kept and enforced server-side, and a browser saying otherwise changes
    nothing."""

    is_new_client: bool
    channel: Channel
    phone: Optional[str] = None
    has_active_assignment: bool = False
    updates_remaining: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None


class FormSubmissionResult(BaseModel):
    """The answer to a submit. Three outcomes, and the page shows a
    genuinely different thing for each:

      - "ok"            -> saved; the confirmation message goes out on a
                           background thread straight afterwards.
      - "locked"        -> deliberately NOT saved: this client has a site
                           visit assigned to an agent, so their requirements
                           are frozen until a human changes them (see
                           assignment_lock_service.py). `message` is what to
                           show.
      - "limit_reached" -> deliberately NOT saved: this identity has used up
                           its allowance of online updates (see
                           inquiry_form_service.MAX_REQUIREMENT_SUBMISSIONS).
                           `message` is what to show, and no WhatsApp
                           message is sent for it — see that module for why.

    A refusal is a 200 with a status, not an HTTP error — nothing went
    wrong, the answer is just "no, and here's why", and the page needs the
    explanation rather than a red failure box telling them to try again."""

    status: str
    message: Optional[str] = None
