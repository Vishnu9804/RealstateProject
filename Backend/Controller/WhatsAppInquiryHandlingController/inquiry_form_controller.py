"""HTTP routes for the whatsappInquiryHandling registration/update form —
the backend side of the link sent by Service/WhatsAppInquiryHandlingService/
inquiry_pipeline_service.py's welcome and update-confirmation WhatsApp
messages. Kept separate from whatsapp_inquiry_controller.py because this is
a public-facing endpoint (identity comes from the URL token, not any
authenticated session) hit by the form page, not an internal management API.
"""

from typing import Tuple

from fastapi import APIRouter, HTTPException

from Model.WhatsAppInquiryHandlingModel.form_submission import (
    Channel,
    FormPrefillResponse,
    FormSubmissionRequest,
    FormSubmissionResult,
)
from Model.WhatsAppInquiryHandlingModel.phone_verification import VerifiedPrefillRequest
from Service.WhatsAppInquiryHandlingService import form_token_service, inquiry_form_service

router = APIRouter(prefix="/whatsapp-inquiry/form", tags=["whatsapp-inquiry-form"])


def _resolve_or_404(token: str) -> Tuple[Channel, str]:
    resolved = form_token_service.resolve_token(token)
    if resolved is None:
        raise HTTPException(status_code=404, detail="This link is invalid or has expired.")
    return resolved


# Declared BEFORE the "/{token}" routes below: "/public" is a single path
# segment and would otherwise be captured as a token by them.
@router.post("/public", response_model=FormSubmissionResult)
def submit_public_form(submission: FormSubmissionRequest) -> FormSubmissionResult:
    """Tokenless submission from the public landing site's requirements form
    (LandingPage/src/components/RequirementsForm.tsx), where the visitor
    arrived from the website or an Instagram bio rather than from a link we
    minted for them.

    Identity is the WhatsApp number they gave — preferably one they proved
    with a 4-digit code (`verification_token`), falling back to the typed
    number when we had no connected WhatsApp number to send a code from. See
    inquiry_form_service.submit_public_form for why that fallback exists."""
    result = inquiry_form_service.submit_public_form(submission)
    if result is None:
        raise HTTPException(status_code=400, detail="Please enter a valid WhatsApp number.")
    return result


# Two path segments, so — unlike "/public" — this one could never be
# mistaken for a token by the routes below whatever order they're in.
@router.post("/public/prefill", response_model=FormPrefillResponse)
def get_verified_prefill(body: VerifiedPrefillRequest) -> FormPrefillResponse:
    """What we already hold for a number this browser has VERIFIED, so a
    returning visitor sees their own saved requirements instead of an empty
    form. POST rather than GET purely so the token travels in the body and
    never lands in a URL (browser history, referrer headers, server logs).

    401, not 404, when the token no longer resolves: the page's answer is to
    ask for the number again, not to report an error."""
    prefill = inquiry_form_service.get_verified_prefill(body.verification_token)
    if prefill is None:
        raise HTTPException(status_code=401, detail="This verification has expired. Please confirm your number again.")
    return prefill


@router.get("/{token}", response_model=FormPrefillResponse)
def get_form_prefill(token: str) -> FormPrefillResponse:
    channel, identity = _resolve_or_404(token)
    return inquiry_form_service.get_prefill(channel, identity)


@router.post("/{token}", response_model=FormSubmissionResult)
def submit_form(token: str, submission: FormSubmissionRequest) -> FormSubmissionResult:
    channel, identity = _resolve_or_404(token)
    return inquiry_form_service.submit_form(channel, identity, submission)
