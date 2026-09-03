"""HTTP routes for the whatsappInquiryHandling registration/update form —
the backend side of the link sent by Service/WhatsAppInquiryHandlingService/
inquiry_pipeline_service.py's welcome and update-confirmation WhatsApp
messages. Kept separate from whatsapp_inquiry_controller.py because this is
a public-facing endpoint (identity comes from the URL token, not any
authenticated session) hit by the form page, not an internal management API.
"""

from fastapi import APIRouter, HTTPException

from Model.WhatsAppInquiryHandlingModel.form_submission import FormPrefillResponse, FormSubmissionRequest
from Service.WhatsAppInquiryHandlingService import form_token_service, inquiry_form_service

router = APIRouter(prefix="/whatsapp-inquiry/form", tags=["whatsapp-inquiry-form"])


def _resolve_or_404(token: str) -> str:
    phone = form_token_service.resolve_token(token)
    if phone is None:
        raise HTTPException(status_code=404, detail="This link is invalid or has expired.")
    return phone


@router.get("/{token}", response_model=FormPrefillResponse)
def get_form_prefill(token: str) -> FormPrefillResponse:
    phone = _resolve_or_404(token)
    record, is_new_client = inquiry_form_service.get_prefill(phone)
    if record is None:
        return FormPrefillResponse(is_new_client=is_new_client)
    return FormPrefillResponse(
        is_new_client=is_new_client,
        **record.model_dump(exclude={"phone", "status", "pending_action", "created_at", "updated_at"}),
    )


@router.post("/{token}")
def submit_form(token: str, submission: FormSubmissionRequest) -> dict:
    phone = _resolve_or_404(token)
    inquiry_form_service.submit_form(phone, submission)
    return {"status": "ok"}
