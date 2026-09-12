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
from Service.WhatsAppInquiryHandlingService import known_client_cache, outbound_messenger
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

# Skip the code entirely for a number we already hold a client record for.
#
# The reasoning FOR it: someone whose number is already in our books has,
# by definition, already reached us on WhatsApp — we have messaged that
# number and they have replied. Asking them to prove ownership of it again
# every time they open the site is friction paid by the people we most want
# to hear from.
#
# The cost, stated plainly because it is real: this is the one place the
# guarantee in this module's docstring is deliberately relaxed. For a
# number that is ALREADY a client, knowing the number becomes enough to be
# treated as its owner — so someone who knows a client's phone number could
# read back and overwrite that client's saved requirements without ever
# holding the phone. Every OTHER number is unaffected and still has to pass
# a code, which means the exposure is exactly the set of people already in
# the client table and nothing wider.
#
# It is a single flag rather than a scattering of conditions so that
# reversing the decision is one line: set this to False and every number
# goes back to being sent a code, with no other change anywhere.
AUTO_CONFIRM_KNOWN_CLIENTS = True

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

      - "verified"    -> no code needed; this number is confirmed as of
                         right now and `token` is the proof. Two things
                         produce it, both described on request_otp: the
                         caller already held a live verification for this
                         same number, or the number is already one of our
                         clients. The dialog never gets as far as showing
                         its boxes.
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
    # Only ever set alongside status "verified" — the same token
    # verify_otp would have minted, reached without a code.
    token: Optional[str] = None


# Ceilings on the three tables below, with a sweep that runs on the way
# into request_otp and verify_otp.
#
# All three used to grow and never shrink. An OTP entry was only removed
# when that number's code was used or re-requested; a send-history entry
# never at all; a verification only when its own token was presented after
# expiry. So every distinct number this endpoint was ever asked about left
# something behind permanently — and since the endpoint is public, "every
# distinct number" is as large as somebody wants to make it. The per-IP
# limiter (Middleware/public_rate_limit.py) caps the rate of that, but a
# rate limit on an unbounded accumulation only decides how long the leak
# takes.
#
# The sweep is cheap and self-limiting: it does nothing at all until a
# table is over its ceiling, and what it removes first is entries that are
# already dead — expired codes, expired verifications, send timestamps
# older than the window they are measured in. Only a table still over its
# ceiling after that loses live entries, oldest first.
_MAX_TRACKED_NUMBERS = 20_000
_MAX_LIVE_VERIFICATIONS = 50_000

_lock = threading.Lock()
_otps: Dict[str, _OtpEntry] = {}
_send_history: Dict[str, List[float]] = {}
_verifications: Dict[str, _VerificationEntry] = {}


def _sweep_locked(now: float) -> None:
    """Called with _lock held. Every branch is skipped entirely in the
    normal case, so this costs one length comparison per request."""
    if len(_otps) > _MAX_TRACKED_NUMBERS:
        for phone in [p for p, entry in _otps.items() if entry.expires_at < now]:
            del _otps[phone]
        for phone in sorted(_otps, key=lambda p: _otps[p].expires_at)[: max(0, len(_otps) - _MAX_TRACKED_NUMBERS)]:
            del _otps[phone]

    if len(_send_history) > _MAX_TRACKED_NUMBERS:
        for phone, stamps in list(_send_history.items()):
            # A number whose sends have all aged out of the window is
            # indistinguishable from one that never sent anything, so the
            # row carries no information at all any more.
            kept = [t for t in stamps if now - t < _SEND_WINDOW_SECONDS]
            if kept:
                _send_history[phone] = kept
            else:
                del _send_history[phone]
        for phone in sorted(_send_history, key=lambda p: _send_history[p][-1])[
            : max(0, len(_send_history) - _MAX_TRACKED_NUMBERS)
        ]:
            del _send_history[phone]

    if len(_verifications) > _MAX_LIVE_VERIFICATIONS:
        for token in [t for t, entry in _verifications.items() if entry.expires_at < now]:
            del _verifications[token]
        for token in sorted(_verifications, key=lambda t: _verifications[t].expires_at)[
            : max(0, len(_verifications) - _MAX_LIVE_VERIFICATIONS)
        ]:
            del _verifications[token]


def request_otp(raw_phone: str, prior_token: Optional[str] = None) -> OtpRequestResult:
    """Mints (or reuses) a code for `raw_phone` and dispatches it over
    WhatsApp. Returns as soon as the code is stored — the send itself runs
    on a daemon thread, because the visitor is staring at a dialog waiting
    for it to open and a WhatsApp round trip is the one slow part of this.
    Whether a connection exists at all is an in-memory check, so the
    "unavailable" answer above is still instant and honest.

    Two shortcuts come FIRST, and both answer "verified" without sending
    anything at all. They are the difference between confirming a number
    once and confirming it every time somebody taps the button:

      1. `prior_token` — a verification this same browser already holds. If
         it resolves to this very number, there is nothing left to prove:
         the browser passed a code for it and the proof has not expired.
         Re-typing a number you just confirmed, on a site that already has
         your proof in hand, must never cost a second WhatsApp message.
         This is a pure in-memory check against a bearer token only that
         browser has, so it can neither be spoofed by typing a number nor
         cost anything to serve.

      2. A number that is ALREADY one of our clients (see
         AUTO_CONFIRM_KNOWN_CLIENTS above for what that trades away, and
         known_client_cache.py for why asking cannot become a database
         bill).

    Everything below them is unchanged: same per-number cooldown, same
    hourly send cap, same reused-while-alive code. Those limits are what
    stop this endpoint being used to send messages to a number somebody
    else owns, and nothing here weakens them — the shortcuts REMOVE sends
    rather than adding any.
    """
    phone = normalize_phone(raw_phone)
    if phone is None:
        return OtpRequestResult(status="invalid")

    # Free, and the single most common repeat case: the visitor changed
    # their mind about the number, then changed it back.
    if prior_token and resolve_verification(prior_token) == phone:
        step_logger.info(f"[OTP] {phone}: already verified by this browser — no code needed.")
        return OtpRequestResult(status="verified", phone=phone, token=_mint_verification(phone))

    if AUTO_CONFIRM_KNOWN_CLIENTS and known_client_cache.is_known_client(phone):
        step_logger.info(f"[OTP] {phone}: already one of our clients — confirmed without a code.")
        return OtpRequestResult(status="verified", phone=phone, token=_mint_verification(phone))

    if whatsapp_connection_manager.get_sender_client(prefer_role="inquiry") is None:
        step_logger.error(f"[OTP] Cannot verify {phone}: no connected WhatsApp number to send from.")
        return OtpRequestResult(status="unavailable", phone=phone)

    now = time.monotonic()
    with _lock:
        _sweep_locked(now)
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
        _sweep_locked(now)
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

    token = _mint_verification(phone)
    step_logger.success(f"[OTP] {phone}: number verified on the public site.")
    return token


def _mint_verification(phone: str) -> str:
    """A fresh 30-day proof that this browser owns `phone`.

    The one place a verification token is ever created, so the three ways
    of arriving at one — passing a code, presenting an unexpired earlier
    proof, and being a known client — cannot drift apart in what they hand
    back. Takes the lock itself, so callers must not already hold it."""
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        _sweep_locked(now)
        _verifications[token] = _VerificationEntry(phone=phone, expires_at=now + _VERIFICATION_TTL_SECONDS)
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
