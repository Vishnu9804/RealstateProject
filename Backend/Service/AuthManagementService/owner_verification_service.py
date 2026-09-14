"""Proves a sensitive account change is really coming from the owner: a
6-digit code is sent over WhatsApp to ADMIN_PHONE (never to a number supplied
by the request). Codes live in memory only.

If ADMIN_PHONE is set but a code can't be sent (no WhatsApp connection, bad
number), verification fails closed — anyone holding an admin session could
otherwise unlink WhatsApp to skip it.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, List, NamedTuple, Optional

from Config.settings import get_settings
from Middleware import step_logger
from Model.AuthManagementModel.user_record import OwnerVerificationStatus, VerificationCodeResult
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager
from Service.WhatsAppInquiryHandlingService import outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

STEP_UP = "step_up"
RECOVERY = "recovery"

_CODE_TTL_SECONDS = 5 * 60
_HOUR = 3600
_DAY = 86400


class _Policy(NamedTuple):
    cooldown_seconds: int
    per_hour: int
    per_day: int
    attempts_per_code: int
    message: str


_POLICIES = {
    STEP_UP: _Policy(
        45, 5, 20, 5,
        "{code} is your Estate Signal owner verification code.\n\n"
        "It approves a change to staff logins and expires in 5 minutes. "
        "If you didn't request it, don't share it with anyone.",
    ),
    RECOVERY: _Policy(
        60, 3, 6, 3,
        "{code} is your Estate Signal password reset code.\n\n"
        "Someone asked to reset the owner password. It expires in 5 minutes. "
        "If this wasn't you, ignore this message and never share the code.",
    ),
}


class _Pending(NamedTuple):
    code: str
    expires_at: float
    attempts: int
    sent_at: float


_lock = threading.Lock()
_pending: Dict[str, _Pending] = {}
_sends: Dict[str, List[float]] = {STEP_UP: [], RECOVERY: []}


def _now() -> float:
    return time.monotonic()


def _owner_phone() -> Optional[str]:
    return normalize_phone(get_settings().admin_phone.strip())


def _can_send() -> bool:
    return whatsapp_connection_manager.get_sender_client(prefer_role="inquiry") is not None


def status() -> OwnerVerificationStatus:
    if not get_settings().admin_phone.strip():
        return OwnerVerificationStatus(method="password", available=True)
    phone = _owner_phone()
    if phone is None:
        return OwnerVerificationStatus(method="whatsapp", available=False, reason="invalid_owner_phone")
    hint = f"••{phone[-2:]}"
    if not _can_send():
        return OwnerVerificationStatus(method="whatsapp", available=False, reason="no_whatsapp_connection", phone_hint=hint)
    return OwnerVerificationStatus(method="whatsapp", available=True, phone_hint=hint)


def request_code(purpose: str) -> VerificationCodeResult:
    current = status()
    if current.method != "whatsapp":
        return VerificationCodeResult(status="not_configured")
    if not current.available:
        return VerificationCodeResult(status="unavailable", phone_hint=current.phone_hint)

    policy = _POLICIES[purpose]
    now = _now()
    with _lock:
        sends = [t for t in _sends[purpose] if now - t < _DAY]
        last_hour = [t for t in sends if now - t < _HOUR]
        existing = _pending.get(purpose)
        alive = existing is not None and existing.expires_at > now
        wait = 0.0
        if alive and now - existing.sent_at < policy.cooldown_seconds:
            wait = policy.cooldown_seconds - (now - existing.sent_at)
        elif len(last_hour) >= policy.per_hour:
            wait = _HOUR - (now - last_hour[0])
        elif len(sends) >= policy.per_day:
            wait = _DAY - (now - sends[0])
        _sends[purpose] = sends
        if wait > 0:
            return VerificationCodeResult(status="cooldown", retry_after_seconds=int(wait) + 1, phone_hint=current.phone_hint)
        code = existing.code if alive else str(100000 + secrets.randbelow(900000))
        sends.append(now)
        _pending[purpose] = _Pending(code, now + _CODE_TTL_SECONDS, existing.attempts if alive else 0, now)

    text = policy.message.format(code=code)
    threading.Thread(target=_deliver, args=(_owner_phone(), text, purpose), name="owner-code", daemon=True).start()
    return VerificationCodeResult(status="sent", phone_hint=current.phone_hint)


def verify_code(purpose: str, code: str) -> bool:
    submitted = (code or "").strip()
    policy = _POLICIES[purpose]
    now = _now()
    with _lock:
        entry = _pending.get(purpose)
        if entry is None or entry.expires_at <= now:
            _pending.pop(purpose, None)
            return False
        if submitted.isascii() and secrets.compare_digest(entry.code, submitted):
            del _pending[purpose]
            return True
        if entry.attempts + 1 >= policy.attempts_per_code:
            del _pending[purpose]
        else:
            _pending[purpose] = entry._replace(attempts=entry.attempts + 1)
        return False


def _deliver(phone: str, text: str, purpose: str) -> None:
    label = "verification" if purpose == STEP_UP else "password reset"
    if outbound_messenger.send_text(phone, text):
        step_logger.info(f"Owner {label} code sent over WhatsApp.")
    else:
        step_logger.error(f"Could not send the owner {label} code over WhatsApp.")
