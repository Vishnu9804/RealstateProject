"""Request/response shapes for the public site's WhatsApp-number
verification — see Service/WhatsAppInquiryHandlingService/otp_service.py
for what the flow is actually for, and
Controller/WhatsAppInquiryHandlingController/phone_verification_controller.py
for the routes that use these.
"""

from typing import Optional

from pydantic import BaseModel, Field


class OtpRequest(BaseModel):
    """`phone` as the visitor typed it — spaces, dashes and a country code
    or not. It is normalized to E.164 server-side (phone_utils.py), never
    in the browser, so the number a code is sent to and the number a
    submission is later saved against are canonicalised by exactly one
    piece of code."""

    phone: str = Field(min_length=6, max_length=24)


class OtpRequestResponse(BaseModel):
    """`status` is "sent", "cooldown", "invalid" or "unavailable" — see
    otp_service.OtpRequestResult for what each one means and, in
    particular, why "unavailable" is not an error the visitor is stopped
    by."""

    status: str
    phone: Optional[str] = None
    retry_after_seconds: int = 0


class OtpVerifyRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=24)
    code: str = Field(min_length=1, max_length=8)


class OtpVerifyResponse(BaseModel):
    """The token the browser stores. It is this browser's proof that it
    passed the OTP for `phone`; every later call that reads or writes that
    number's data sends it back."""

    verification_token: str
    phone: str
    expires_in_seconds: int


class VerifiedPrefillRequest(BaseModel):
    verification_token: str = Field(min_length=1, max_length=200)
