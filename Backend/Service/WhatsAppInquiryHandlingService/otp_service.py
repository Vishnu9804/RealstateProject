"""Proves that whoever typed a WhatsApp number into the PUBLIC site
actually owns it, by sending a 4-digit code to that number and asking for
it back.

Why this exists: a visitor who arrives from our own WhatsApp message
carries a form token, and that token IS their identity (see
form_token_service.py) — nothing they type can change who a submission is
attributed to. A visitor who found the site through a browser search or an
Instagram bio has no such token, so the only identity available is the
number they type — and an unverified typed number can be anyone's. Without
this module, a stranger could overwrite (or, worse, read back) a real
client's requirements just by typing their number. This is the missing
half of the guarantee form_token_service.py's docstring describes.

Two short-lived things live here, and they are deliberately different:

  - the OTP itself: one code per phone number, 5 minutes, a handful of
    attempts. Consumed the moment it is used correctly.
  - the VERIFICATION token minted on success: the browser's proof that it
    once passed the OTP for that number, kept for 30 days so a returning
    visitor on the same browser is not challenged again on every visit
    (the public site's whole point is that it is frictionless). It is a
    bearer secret held only in that one browser's localStorage, exactly
    like a session cookie.

In-memory only, for the same reason form_token_service.py is: these are
cheap to reissue and losing them on a restart costs a visitor one extra
OTP, not any durable data. A verification token that no longer resolves is
treated by the site as "not verified yet", never as an error.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, List, NamedTuple, Optional

from Middleware import step_logger
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager
from Service.WhatsAppInquiryHandlingService import outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_OTP_TTL_SECONDS = 5 * 60
# Two different limits doing two different jobs: the cooldown stops a
# double-clicked "Resend" from sending two messages, the window cap stops
# this endpoint being used to spam a number someone else owns.
_RESEND_COOLDOWN_SECONDS = 30
_SEND_WINDOW_SECONDS = 60 * 60
_MAX_SENDS_PER_WINDOW = 5
_MAX_ATTEMPTS = 5

_VERIFICATION_TTL_SECONDS = 30 * 24 * 60 * 60

# The code leads the message on purpose: a WhatsApp notification preview
# shows the first line, so this is readable without opening the chat —
# which is the difference between "quick" and "switch apps, find the chat,
# come back".
_OTP_TEXT_TEMPLATE = (
    "{code} is your Manibhadra Real Estate verification code.\n\n"
    "Welcome! Enter this code on our website to confirm your WhatsApp number, "
    "and we'll send you the properties that match what you're looking for.\n"
    "The code is valid for 5 minutes. If this wasn't you, please ignore this message."
)


class _OtpEntry(NamedTuple):
    code: str
    expires_at: float
    attempts: int
    last_sent_at: float


class _VerificationEntry(NamedTuple):
    phone: str
    expires_at: float


class OtpRequestResult(NamedTuple):
    """`status` is what the browser branches on:

      - "sent"        -> the dialog opens and asks for the code.
      - "invalid"     -> the number isn't a phone number at all.
      - "cooldown"    -> a code was just sent; the existing one still works.
      - "unavailable" -> no WhatsApp connection is currently linked, so we
                         physically cannot send anything. The site falls
                         back to its previous, unverified behaviour rather
                         than dead-ending a real visitor over our own
                         infrastructure being down — losing the enquiry
                         would be strictly worse than accepting it
                         unverified, which is all that ever happened before
                         this module existed.
    """

    status: str
    phone: Optional[str] = None
    retry_after_seconds: int = 0


_lock = threading.Lock()
_otps: Dict[str, _OtpEntry] = {}
_send_history: Dict[str, List[float]] = {}
_verifications: Dict[str, _VerificationEntry] = {}


def request_otp(raw_phone: str) -> OtpRequestResult:
    """Mints (or reuses) a code for `raw_phone` and dispatches it over
    WhatsApp. Returns as soon as the code is stored — the send itself runs
    on a daemon thread, because the visitor is staring at a dialog waiting
    for it to open and a WhatsApp round trip is the one slow part of this.
    Whether a connection exists at all is an in-memory check, so the
    "unavailable" answer above is still instant and honest."""
    phone = normalize_phone(raw_phone)
    if phone is None:
        return OtpRequestResult(status="invalid")

    if whatsapp_connection_manager.get_sender_client(prefer_role="inquiry") is None:
        step_logger.error(f"[OTP] Cannot verify {phone}: no connected WhatsApp number to send from.")
        return OtpRequestResult(status="unavailable", phone=phone)

    now = time.monotonic()
    with _lock:
        existing = _otps.get(phone)
        alive = existing is not None and existing.expires_at > now

        if alive and now - existing.last_sent_at < _RESEND_COOLDOWN_SECONDS:
            return OtpRequestResult(
                status="cooldown",
                phone=phone,
                retry_after_seconds=int(_RESEND_COOLDOWN_SECONDS - (now - existing.last_sent_at)) + 1,
            )

        history = [t for t in _send_history.get(phone, []) if now - t < _SEND_WINDOW_SECONDS]
        if len(history) >= _MAX_SENDS_PER_WINDOW:
            step_logger.info(f"[OTP] {phone}: send limit reached for this hour — not sending another code.")
            return OtpRequestResult(
                status="cooldown",
                phone=phone,
                retry_after_seconds=int(_SEND_WINDOW_SECONDS - (now - history[0])) + 1,
            )

        # The SAME code is reused while one is still alive: a visitor who
        # hits Resend should be able to use whichever of the two messages
        # they happen to open, not have the first one silently invalidated.
        code = existing.code if alive else _generate_code()
        history.append(now)
        _send_history[phone] = history
        _otps[phone] = _OtpEntry(
            code=code,
            expires_at=now + _OTP_TTL_SECONDS,
            attempts=existing.attempts if alive else 0,
            last_sent_at=now,
        )

    threading.Thread(target=_send_code, args=(phone, code), name="otp-send", daemon=True).start()
    return OtpRequestResult(status="sent", phone=phone)


def verify_otp(raw_phone: str, code: str) -> Optional[str]:
    """Returns a verification token when `code` is the live code for
    `raw_phone`, or None for every failure (wrong code, expired, too many
    attempts, unparseable number) — the caller deliberately does not get to
    tell those apart, so this can't be used to probe which numbers have a
    code outstanding."""
    phone = normalize_phone(raw_phone)
    if phone is None:
        return None
    submitted = (code or "").strip()

    now = time.monotonic()
    with _lock:
        entry = _otps.get(phone)
        if entry is None or entry.expires_at < now:
            _otps.pop(phone, None)
            return None
        if entry.attempts >= _MAX_ATTEMPTS:
            del _otps[phone]
            return None
        if not secrets.compare_digest(entry.code, submitted):
            _otps[phone] = entry._replace(attempts=entry.attempts + 1)
            return None

        # Correct: the code is spent immediately, so one code can never
        # mint two verification tokens.
        del _otps[phone]
        token = secrets.token_urlsafe(24)
        _verifications[token] = _VerificationEntry(phone=phone, expires_at=now + _VERIFICATION_TTL_SECONDS)

    step_logger.success(f"[OTP] {phone}: number verified on the public site.")
    return token


def resolve_verification(token: Optional[str]) -> Optional[str]:
    """The E.164 number `token` was minted for, or None if it is unknown or
    expired. Never raises — an unknown token means "this browser is not
    verified", which is a normal state, not an error."""
    if not token:
        return None
    with _lock:
        entry = _verifications.get(token)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            del _verifications[token]
            return None
        return entry.phone


def verification_ttl_seconds() -> int:
    """So the browser can expire its stored token on its own clock instead
    of discovering it is stale mid-submission."""
    return _VERIFICATION_TTL_SECONDS


def _generate_code() -> str:
    """Four digits, uniformly over 1000-9999 — never leading-zero-padded,
    because a code shown as "0421" in one place and typed as "421" in
    another is a support call waiting to happen."""
    return str(1000 + secrets.randbelow(9000))


def _send_code(phone: str, code: str) -> None:
    if outbound_messenger.send_text(phone, _OTP_TEXT_TEMPLATE.format(code=code)):
        step_logger.success(f"[OTP] {phone}: verification code sent.")
    else:
        # Not fatal: the stored code is still valid, and the dialog's own
        # Resend button is the recovery path.
        step_logger.error(f"[OTP] {phone}: FAILED to send the verification code.")
