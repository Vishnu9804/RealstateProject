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

    # A verification this browser ALREADY holds, sent back so the server
    # can recognise "this is the same number I confirmed a moment ago" and
    # answer without another WhatsApp message. Optional in every sense: a
    # browser that has never verified anything simply omits it, and one
    # that sends a stale or unrelated token is treated exactly as if it had
    # sent nothing. It is never used as the identity of a submission — only
    # to decide whether a code needs sending.
    verification_token: Optional[str] = Field(default=None, max_length=200)


class OtpRequestResponse(BaseModel):
    """`status` is "verified", "sent", "cooldown", "invalid" or
    "unavailable" — see otp_service.OtpRequestResult for what each one
    means and, in particular, why "unavailable" is not an error the visitor
    is stopped by.

    `verification_token` / `expires_in_seconds` are populated for
    "verified" and only for "verified": that status means no code is
    needed, so the browser is handed the very same proof a successful
    /confirm would have given it and carries on as if it had typed one."""

    status: str
    phone: Optional[str] = None
    retry_after_seconds: int = 0
    verification_token: Optional[str] = None
    expires_in_seconds: int = 0


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
