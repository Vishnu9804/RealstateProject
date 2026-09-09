"""HTTP routes for verifying a WhatsApp number typed into the PUBLIC site
(LandingPage/) — the two calls behind the 4-digit code dialog.

Public and unauthenticated, exactly like inquiry_form_controller.py's
"/public" route, and for the same reason: the caller is an anonymous
visitor who has no link of ours. The difference this router makes is that,
by the end of it, the number they typed is one they demonstrably control —
see Service/WhatsAppInquiryHandlingService/otp_service.py.

Deliberately says as little as possible on failure. "That code isn't
right" is the same answer for a wrong code, an expired one, and a number
that never had one, so nothing here can be used to find out which numbers
are in our system.
"""

from fastapi import APIRouter, HTTPException

from Model.WhatsAppInquiryHandlingModel.phone_verification import (
    OtpRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    OtpVerifyResponse,
)
from Service.WhatsAppInquiryHandlingService import otp_service

router = APIRouter(prefix="/whatsapp-inquiry/verify", tags=["whatsapp-inquiry-verify"])


@router.post("/request", response_model=OtpRequestResponse)
def request_code(body: OtpRequest) -> OtpRequestResponse:
    """Sends a 4-digit code to `body.phone` over WhatsApp.

    Returns 200 for every outcome except an unparseable number, including
    "unavailable" (no linked WhatsApp connection to send from). That is not
    an oversight: the browser needs to tell "we couldn't send you a code, so
    carry on without verifying" apart from "something broke", and only a
    successful response with a status in it can say that."""
    result = otp_service.request_otp(body.phone)
    if result.status == "invalid":
        raise HTTPException(status_code=400, detail="That doesn't look like a valid WhatsApp number.")
    return OtpRequestResponse(
        status=result.status,
        phone=result.phone,
        retry_after_seconds=result.retry_after_seconds,
    )


@router.post("/confirm", response_model=OtpVerifyResponse)
def confirm_code(body: OtpVerifyRequest) -> OtpVerifyResponse:
    token = otp_service.verify_otp(body.phone, body.code)
    if token is None:
        raise HTTPException(status_code=400, detail="That code isn't right, or it has expired. Please try again.")
    phone = otp_service.resolve_verification(token)
    return OtpVerifyResponse(
        verification_token=token,
        # The canonical E.164 form, not what they typed — this is the
        # number every later call will be about, so it is the one the
        # browser should display and store.
        phone=phone or body.phone,
        expires_in_seconds=otp_service.verification_ttl_seconds(),
    )
