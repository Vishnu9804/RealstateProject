"""HTTP routes for the whatsappInquiryHandling registration/update form —
the backend side of the link sent by Service/WhatsAppInquiryHandlingService/
inquiry_pipeline_service.py's welcome and update-confirmation WhatsApp
messages. Kept separate from whatsapp_inquiry_controller.py because this is
a public-facing endpoint (identity comes from the URL token, not any
authenticated session) hit by the form page, not an internal management API.
"""

from typing import Tuple

from fastapi import APIRouter, HTTPException

from Model.WhatsAppInquiryHandlingModel.form_submission import Channel, FormPrefillResponse, FormSubmissionRequest
from Service.WhatsAppInquiryHandlingService import form_token_service, inquiry_form_service

router = APIRouter(prefix="/whatsapp-inquiry/form", tags=["whatsapp-inquiry-form"])


def _resolve_or_404(token: str) -> Tuple[Channel, str]:
    resolved = form_token_service.resolve_token(token)
    if resolved is None:
        raise HTTPException(status_code=404, detail="This link is invalid or has expired.")
    return resolved


@router.get("/{token}", response_model=FormPrefillResponse)
def get_form_prefill(token: str) -> FormPrefillResponse:
    channel, identity = _resolve_or_404(token)
    return inquiry_form_service.get_prefill(channel, identity)


@router.post("/{token}")
def submit_form(token: str, submission: FormSubmissionRequest) -> dict:
    channel, identity = _resolve_or_404(token)
    inquiry_form_service.submit_form(channel, identity, submission)
    return {"status": "ok"}
