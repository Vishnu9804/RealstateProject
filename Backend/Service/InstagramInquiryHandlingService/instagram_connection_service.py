"""Manages the client's Instagram connection — a plain username/password
login (via instagrapi's private-API client), the same "connect once, stays
connected" shape as WhatsApp's QR pairing, but for Instagram.

This is deliberately NOT Meta's official Graph API/OAuth. That would need a
Meta developer app, App Review, and a registered HTTPS redirect URI set up
per client before anyone could connect — the opposite of "the client just
hands me their Instagram ID and password, like WhatsApp's QR code." What
instagrapi does instead is log in exactly like the real Instagram mobile
app would (same private endpoints), which is why it only needs a
username/password, but it is unofficial and against Instagram's own Terms
of Service — there's a real, non-zero chance an account gets challenged
more often or temporarily restricted for automated access. That trade-off
is a deliberate, informed product decision, not an oversight; the Instagram
tab's UI carries a short version of this same warning next to the login
form, since a future client of this app won't have read this comment.

Session lifecycle, mirroring WhatsApp's "pair once, then persist" model:
  1. connect(username, password) logs in on a background thread (never the
     request thread — a real login round-trip is a slow, blocking network
     call, same reasoning as WhatsApp's client running on its own thread in
     Service/WhatsAppDataFetchingService/whatsapp_service.py).
  2. Instagram may respond with:
       - TwoFactorRequired — the account's own 2FA. Resolved by asking the
         client for the code and calling login() again with it.
       - ChallengeRequired, resolvable kind — Instagram emailed/texted a
         one-time code because it doesn't recognize this login. Resolved
         the same way, via instagrapi's challenge_code_handler.
       - ChallengeRequired, manual kind (several sub-variants: Bloks
         redirect, native challenge flow, auth-platform flow, ...) —
         Instagram wants the client to approve THIS login from inside the
         real Instagram app first; no code exists to type in. Reaching this
         branch at all means instagrapi already tried its own automatic
         resolution and gave up (see _attempt_login's ChallengeRequired
         handler) — every sub-variant gets the same UI, but only the Bloks
         one has a programmatic "resume this exact client" API; see
         _is_bloks_redirect_challenge and _retry_worker.
     All three pause the attempt and surface a stage the UI can act on
     (awaiting_code / manual_verification_required) instead of just
     failing outright.
  3. Every attempt reuses the SAME device identity (uuids/user-agent/etc.)
     for a given username across retries — instagrapi's own guidance for
     avoiding exactly this kind of challenge is "retry with the same saved
     client settings, device identifiers, and proxy/IP", so a fresh random
     device on every attempt would make challenges MORE likely, not less.
  4. On success, the *session* (instagrapi's device/auth state — never the
     password, which is only ever held in memory for the duration of one
     connect attempt) is saved through Database/settings_repository.py, the
     same generic key-value store area_filter_service/display_settings_service/
     duplicate_detection_service already use, so a backend restart reuses
     it instead of asking the client to log in again.
  5. A background loop periodically makes one cheap authenticated call to
     confirm the session is still good (and keep it warm) — if Instagram
     ever invalidates it, status flips back to "disconnected" with a clear
     reason instead of failing silently the next time it's needed.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional

from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, ClientError, TwoFactorRequired

from Database import settings_repository
from Database.session import is_database_configured
from Middleware import step_logger

_SETTINGS_KEY = "instagram_connection"
_DEVICE_KEY_PREFIX = "instagram_device:"
_VERIFICATION_TIMEOUT_SECONDS = 5 * 60
_KEEPALIVE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60

_lock = threading.Lock()
_client: Optional[Client] = None  # the live, logged-in client — None until connected
_state: dict = {
    # disconnected | connecting | awaiting_code | manual_verification_required | connected | error
    "stage": "disconnected",
    "code_kind": None,  # "2fa" | "challenge" | None
    "code_choice": None,  # e.g. "email"/"sms", only meaningful for a resolvable challenge
    "username": None,
    "connected_at": None,
    "last_verified_at": None,
    "error_message": None,
}

# One pending verification wait at a time — this app connects exactly one
# Instagram account at a time (same single-tenant assumption the rest of
# the app makes), so there is never more than one login attempt in flight.
_pending_event: Optional[threading.Event] = None
_pending_code: Optional[str] = None

# Held only while a Bloks "approve this in the real Instagram app" checkpoint
# is pending — the one case that can't be resolved with a typed code, only
# by retrying the SAME client after the client approves out-of-band. Cleared
# the moment the attempt succeeds, fails, or a fresh connect()/disconnect()
# supersedes it. The password is kept only for this short, bounded window
# (never written to disk) so the retry can call login() again without
# asking the client to re-type it.
_pending_client: Optional[Client] = None
_pending_password: Optional[str] = None


def _update_state(**fields) -> None:
    with _lock:
        _state.update(fields)


def get_client() -> Optional[Client]:
    """The live, logged-in instagrapi client — None whenever stage isn't
    "connected". Used by Service/InstagramInquiryHandlingService/
    instagram_messenger.py and instagram_reel_matcher.py to actually talk
    to Instagram; nothing outside this module ever touches _client
    directly, so connect()/disconnect()/the keepalive loop stay the only
    things that can change which client (if any) is "the" live one."""
    with _lock:
        return _client


def get_status() -> dict:
    with _lock:
        return dict(_state)


def connect(username: str, password: str) -> dict:
    """Starts a login attempt on a background thread and returns
    immediately with the "connecting" stage — the caller (the Instagram
    tab) polls get_status() the same way the WhatsApp tab polls for QR/
    pairing progress. Replaces any previous connection attempt/session."""
    _clear_pending_bloks_attempt()
    _update_state(
        stage="connecting",
        code_kind=None,
        code_choice=None,
        username=username,
        error_message=None,
    )
    thread = threading.Thread(target=_connect_worker, args=(username, password), name="instagram-connect", daemon=True)
    thread.start()
    return get_status()


def submit_verification_code(code: str) -> dict:
    """Resumes whichever login attempt is currently paused waiting for a
    2FA or emailed/texted challenge code. Returns the status unchanged
    (still "awaiting_code") if nothing is actually waiting — the caller
    should treat that as "too late, the attempt already timed out or
    finished"."""
    global _pending_code
    with _lock:
        event = _pending_event
        if event is None:
            return dict(_state)
        _pending_code = code.strip()
    event.set()
    return get_status()


def retry_after_manual_approval() -> dict:
    """For the Bloks manual-approval checkpoint only: called once the
    client says they've approved this login inside the real Instagram app.
    Acknowledges the checkpoint on the SAME client/session that hit it, then
    attempts login() again — Instagram should now accept it. Returns the
    status unchanged if there is nothing actually pending."""
    with _lock:
        client = _pending_client
        username = _state.get("username")
        password = _pending_password
    if client is None or username is None or password is None:
        return get_status()

    _update_state(stage="connecting", error_message=None)
    thread = threading.Thread(
        target=_retry_worker, args=(client, username, password), name="instagram-retry-approval", daemon=True
    )
    thread.start()
    return get_status()


def start_over() -> dict:
    """Abandons whatever attempt is in progress (including a pending Bloks
    approval) and returns to the plain login form — the escape hatch when a
    checkpoint isn't going to resolve and the client just wants to try
    again from scratch."""
    _clear_pending_bloks_attempt()
    _update_state(stage="disconnected", code_kind=None, code_choice=None, error_message=None)
    return get_status()


def disconnect() -> dict:
    global _client
    _clear_pending_bloks_attempt()
    with _lock:
        _client = None
    _persist_session(None)
    _update_state(
        stage="disconnected",
        code_kind=None,
        code_choice=None,
        username=None,
        connected_at=None,
        last_verified_at=None,
        error_message=None,
    )
    step_logger.info("Instagram disconnected.")
    return get_status()


def load_from_database() -> None:
    """Mirrors area_filter_service.load_from_database() and its siblings —
    called once at startup (see main.py's lifespan) so a session saved
    before a backend restart is reused instead of asking the client to log
    in again. Verifies the restored session actually still works (Instagram
    can invalidate it independently, e.g. the client logging out on their
    phone) before trusting it."""
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if not stored or not stored.get("settings") or not stored.get("username"):
        return

    global _client
    client = Client()
    try:
        client.set_settings(stored["settings"])
        client.username = stored["username"]
        client.account_info()  # cheapest authenticated call — raises if the session is no longer valid
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Stored Instagram session is no longer valid, reconnect required: {exc!r}")
        return

    with _lock:
        _client = client
    now = datetime.now(timezone.utc).isoformat()
    _update_state(
        stage="connected",
        code_kind=None,
        code_choice=None,
        username=stored["username"],
        connected_at=stored.get("connected_at") or now,
        last_verified_at=now,
        error_message=None,
    )
    step_logger.success(f"Instagram session restored for @{stored['username']} — no re-login needed.")


def start_background_keepalive() -> None:
    """Mirrors whatsapp_service.start_agent_in_background()'s pattern — one
    daemon thread, alive for the process's lifetime. Unlike WhatsApp's
    session (which whatsmeow keeps alive on its own persistent socket),
    instagrapi's session is just stored cookies/tokens with no active
    connection to drop — so "staying connected" here means periodically
    proving the session still works, not reconnecting a dropped socket."""
    thread = threading.Thread(target=_keepalive_loop, name="instagram-keepalive", daemon=True)
    thread.start()


def _keepalive_loop() -> None:
    while True:
        time.sleep(_KEEPALIVE_CHECK_INTERVAL_SECONDS)
        try:
            _check_session_alive()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Instagram keepalive check failed: {type(exc).__name__}: {exc!r}")


# Cooldown for verify_session_now — the polling service calls this every
# time an Instagram API call fails (see instagram_polling_service.py), so
# without a floor a burst of failures (e.g. several comments/threads in one
# poll cycle all hitting the same underlying problem) would fire several
# real account_info() calls back-to-back, adding load to an account that
# may already be rate-limited. One verification per burst is enough to
# update the status; the next genuinely new burst (30s+ later) gets its own.
_MIN_SECONDS_BETWEEN_VERIFICATIONS = 30
_last_verification_attempt: float = 0.0


def verify_session_now(reason: str) -> bool:
    """On-demand version of the 6-hourly keepalive check — called by
    Service/InstagramInquiryHandlingService/instagram_polling_service.py
    the moment an Instagram API call fails during polling, so a dead
    session shows up as "disconnected" in the Connection tab within
    seconds, not up to 6 hours later while the poller silently keeps
    failing the same way every cycle in the meantime. Returns True if the
    session is (still) good, False if it was found dead and the state was
    flipped to disconnected. A no-op (returns True) if nothing is
    connected, or if another verification ran too recently — see
    _MIN_SECONDS_BETWEEN_VERIFICATIONS."""
    global _last_verification_attempt
    now = time.monotonic()
    with _lock:
        client = _client
        if client is None:
            return True
        if now - _last_verification_attempt < _MIN_SECONDS_BETWEEN_VERIFICATIONS:
            return True
        _last_verification_attempt = now

    step_logger.info(f"Verifying the Instagram session is still alive (triggered by: {reason})...")
    return _check_session_alive(client)


def _check_session_alive(client: Optional[Client] = None) -> bool:
    global _client
    if client is None:
        with _lock:
            client = _client
        if client is None:
            return True
    try:
        client.account_info()
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"Instagram session was invalidated ({type(exc).__name__}: {exc}) — reconnecting required."
        )
        with _lock:
            _client = None
        _update_state(
            stage="disconnected",
            username=None,
            error_message="Instagram ended this session — please connect again.",
        )
        return False
    _update_state(last_verified_at=datetime.now(timezone.utc).isoformat())
    return True


# --- the actual login flow, run entirely on a background thread -----------


def _connect_worker(username: str, password: str) -> None:
    client = _build_client(username)
    client.challenge_code_handler = _challenge_code_handler
    _attempt_login(client, username, password)


def _retry_worker(client: Client, username: str, password: str) -> None:
    """Resumes after a manual-approval checkpoint (see
    retry_after_manual_approval()). Only the Bloks redirect kind has a real
    "acknowledge and continue on the same client" API
    (challenge_bloks_redirect_dismiss) — every other manual checkpoint
    instagrapi surfaces (native challenge flow, auth-platform flow, ...) has
    no such resume; instagrapi's own guidance for those is just "retry with
    the same saved client settings, device identifiers, and proxy/IP", i.e.
    a plain fresh login attempt, which is exactly what _build_client's
    per-username device-identity reuse already sets up."""
    if _is_bloks_redirect_challenge(client):
        try:
            client.challenge_bloks_redirect_dismiss()
        except Exception as exc:  # noqa: BLE001
            _fail(f"Instagram still won't accept this login: {exc}")
            return
        _attempt_login(client, username, password)
        return

    fresh_client = _build_client(username)
    fresh_client.challenge_code_handler = _challenge_code_handler
    _attempt_login(fresh_client, username, password)


def _attempt_login(client: Client, username: str, password: str, verification_code: str = "") -> None:
    global _client
    try:
        try:
            client.login(username, password, verification_code=verification_code)
        except TwoFactorRequired:
            if verification_code:
                # Already retried once with a code and it was still rejected
                # — asking for yet another code without telling the client
                # the last one was wrong would just repeat the same failure.
                _fail("That 2FA code wasn't accepted. Please try connecting again.")
                return
            code = _wait_for_code("2fa", None)
            if code is None:
                _fail("Timed out waiting for the 2FA code.")
                return
            _save_device_settings(username, client)
            _attempt_login(client, username, password, verification_code=code)
            return
    except ChallengeRequired as exc:
        # Reaching here at all means instagrapi already tried to resolve
        # this itself — challenge_code_handler for a typeable code (see
        # _challenge_code_handler), or its own internal Bloks-taking-
        # challenge attempt — and gave up. There is never a code left to
        # ask the client for at this point, only a "go confirm this in the
        # real Instagram app" step; see _retry_worker for what Retry
        # actually does about it.
        _save_device_settings(username, client)
        global _pending_client, _pending_password
        with _lock:
            _pending_client = client
            _pending_password = password
        _update_state(
            stage="manual_verification_required",
            code_kind=None,
            code_choice=None,
            error_message=str(exc) or "Instagram wants this login approved from inside the real Instagram app.",
        )
        return
    except ClientError as exc:
        _save_device_settings(username, client)
        _fail(_friendly_login_error(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _save_device_settings(username, client)
        _fail(f"Could not connect to Instagram: {exc}")
        return

    resolved_username = client.username or username
    _save_device_settings(resolved_username, client)
    with _lock:
        _client = client
    _persist_session(client, resolved_username)
    now = datetime.now(timezone.utc).isoformat()
    _update_state(
        stage="connected",
        code_kind=None,
        code_choice=None,
        username=resolved_username,
        connected_at=now,
        last_verified_at=now,
        error_message=None,
    )
    step_logger.success(f"Instagram connected as @{resolved_username}.")


def _is_bloks_redirect_challenge(client: Client) -> bool:
    """True only for the specific Bloks "approve this in the real Instagram
    app" redirect checkpoint — the one manual checkpoint instagrapi exposes
    a programmatic "acknowledge and continue on this same client" API for
    (challenge_bloks_redirect_dismiss). Every other manual checkpoint
    (native challenge flow, auth-platform flow, ...) has no such resume —
    see _retry_worker."""
    last_json = getattr(client, "last_json", None) or {}
    return bool(last_json.get("challenge_context")) and last_json.get("bloks_action") == ChallengeRequired.BLOKS_REDIRECT_ACTION


def _challenge_code_handler(username: str, choice) -> object:
    """Instagram's own account-verification challenge (not 2FA) — invoked
    by instagrapi FROM INSIDE client.login() itself when Instagram doesn't
    recognize this login and emails/texts a code. Must block the calling
    (background) thread until the code arrives; returning False tells
    instagrapi the challenge could not be resolved this way (which usually
    means it's actually one of the manual-approval kinds — see
    _attempt_login's ChallengeRequired handler)."""
    code = _wait_for_code("challenge", str(choice) if choice is not None else None)
    return code if code is not None else False


def _wait_for_code(kind: str, choice: Optional[str]) -> Optional[str]:
    global _pending_event, _pending_code
    event = threading.Event()
    with _lock:
        _pending_event = event
        _pending_code = None
    _update_state(stage="awaiting_code", code_kind=kind, code_choice=choice)

    resolved = event.wait(timeout=_VERIFICATION_TIMEOUT_SECONDS)
    with _lock:
        code = _pending_code if resolved else None
        _pending_event = None
        _pending_code = None
    return code


def _clear_pending_bloks_attempt() -> None:
    global _pending_client, _pending_password
    with _lock:
        _pending_client = None
        _pending_password = None


def _fail(message: str) -> None:
    step_logger.error(f"Instagram connect failed: {message}")
    _update_state(stage="error", code_kind=None, code_choice=None, error_message=message)


def _friendly_login_error(exc: ClientError) -> str:
    name = type(exc).__name__
    if name == "BadPassword":
        return "Incorrect username or password."
    if name == "PleaseWaitFewMinutes":
        return "Instagram is temporarily rate-limiting login attempts — please wait a few minutes and try again."
    # Other exception types (BadCredentials, generic ClientError subclasses,
    # ...) carry a message from instagrapi that's already specific and
    # actionable — surfacing it beats a generic "something went wrong".
    return str(exc) or f"Instagram rejected this login ({name})."


# --- device identity persistence, independent of a confirmed session ------


def _device_key(username: str) -> str:
    return f"{_DEVICE_KEY_PREFIX}{username.strip().lower()}"


def _build_client(username: str) -> Client:
    """A fresh Client, but seeded with the SAME device identity
    (uuids/user-agent/etc.) used on this username's last attempt, if any —
    see the module docstring's point 3. Falls back to instagrapi's own
    freshly-generated device when there is no prior attempt to reuse."""
    client = Client()
    if is_database_configured():
        stored = settings_repository.get_value(_device_key(username))
        if stored:
            try:
                client.set_settings(stored)
                client.username = username
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Could not reuse the saved Instagram device identity for @{username}: {exc!r}")
    return client


def _save_device_settings(username: str, client: Client) -> None:
    """Persists this attempt's device identity (uuids/user-agent/cookies)
    for next time, regardless of whether THIS attempt succeeded — even a
    failed/challenged attempt is worth remembering, since retrying with a
    brand-new random device on every try is exactly what makes Instagram
    more suspicious, not less."""
    if not is_database_configured():
        return
    try:
        settings_repository.set_value(_device_key(username), client.get_settings())
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Could not save the Instagram device identity for @{username}: {exc!r}")


def _persist_session(client: Optional[Client], username: Optional[str] = None) -> None:
    if not is_database_configured():
        return
    if client is None:
        settings_repository.set_value(_SETTINGS_KEY, {})
        return
    with _lock:
        connected_at = _state.get("connected_at") or datetime.now(timezone.utc).isoformat()
    settings_repository.set_value(
        _SETTINGS_KEY,
        {"settings": client.get_settings(), "username": username, "connected_at": connected_at},
    )
