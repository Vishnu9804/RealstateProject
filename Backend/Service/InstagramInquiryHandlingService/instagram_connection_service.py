"""Owns the client's Instagram connection, using Meta's OFFICIAL Instagram
Platform API ("Instagram API with Instagram login").

This replaced an unofficial username/password login (instagrapi, the
private mobile API). That approach worked, but Instagram treats it as
exactly what it is — a real account being driven by a script — and starts
showing "we detected automated behaviour" warnings, extra checkpoints and
eventually restrictions on the very account the business depends on. The
official API has none of that risk, and it also removes the need to poll:
Meta PUSHES an event to us the moment a comment or DM arrives (see
Service/InstagramInquiryHandlingService/instagram_event_service.py), so
nothing in this feature asks Instagram "anything new?" on a timer any more.

What replaces "log in with a username and password":

  1. An access token belonging to the client's Instagram professional
     account, obtained either by
       - the business-login OAuth round trip (build_authorize_url ->
         Instagram's consent screen -> complete_oauth), which is the
         production path, or
       - pasting a token generated in the Meta App Dashboard
         ("Generate token" next to an Instagram Tester account), which is
         the fastest path while developing and needs no redirect URI.
     Both end in the same place: _finish_connect.
  2. That token is swapped for a LONG-LIVED one (60 days) where possible,
     persisted through Database/settings_repository.py — the same generic
     key-value area the WhatsApp/area settings use — and refreshed by a
     background thread well before it expires, so a connection made once
     keeps working indefinitely without anyone logging in again.
  3. The app is subscribed to this account's `comments` and `messages`
     webhook fields, which is what actually starts the event flow.

Note the settings key: `instagram_official_connection`, deliberately NOT
the `instagram_connection` key the old private-API session used. A leftover
row from the old login is a completely different shape and must never be
read back as if it were a token — the new key means an upgrade simply finds
nothing and asks for a fresh connection, instead of failing in a confusing
way on data it cannot understand.
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Tuple
from urllib.parse import urlencode

from Config.settings import get_settings
from Database import settings_repository
from Database.session import is_database_configured
from Middleware import step_logger
from Service.InstagramInquiryHandlingService import instagram_graph_client as graph
from Service.InstagramInquiryHandlingService.instagram_graph_client import InstagramApiError

_SETTINGS_KEY = "instagram_official_connection"

# The webhook fields this feature needs, and only those. `comments` carries
# a new comment on any of the account's media; `messages` carries an
# incoming DM, including a shared reel. Subscribing to more (message_echoes,
# mentions, story_insights, ...) would mean Meta posting events this app
# then has to receive, authenticate and discard — real Railway CPU spent on
# nothing.
WEBHOOK_FIELDS = ("comments", "messages")

# Business-login scopes. `basic` is mandatory; `manage_comments` covers both
# reading the comment that arrives and posting the public reply; and
# `manage_messages` covers sending the DM. Nothing here asks for content
# publishing — this app never posts on the client's behalf.
OAUTH_SCOPES = (
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
)

# A long-lived token lasts 60 days. Refreshing once it is inside this window
# leaves an enormous margin: even if the server is down for three weeks
# straight, the first refresh after it comes back is still in time.
_REFRESH_WHEN_WITHIN = timedelta(days=15)
_MAINTENANCE_INTERVAL_SECONDS = 6 * 60 * 60

# How long an OAuth attempt's `state` stays valid. The whole round trip is
# one consent screen, so ten minutes is generous; anything older is a stale
# tab, not a login in progress.
_OAUTH_STATE_TTL_SECONDS = 600

_lock = threading.Lock()

_access_token: Optional[str] = None
_state: dict = {
    # disconnected | connecting | connected | error
    "stage": "disconnected",
    "username": None,
    # The Instagram professional account id (`user_id` from /me). This is
    # the id that appears as `entry.id` on webhook deliveries and as
    # `from.id` on this account's own comments.
    "ig_user_id": None,
    # The app-scoped id (`id` from /me). Meta has used BOTH of these as the
    # account identifier on webhook payloads depending on app type, so both
    # are kept and both are treated as "us" — see is_self_id.
    "account_id": None,
    "connected_at": None,
    "last_verified_at": None,
    "token_expires_at": None,
    "webhook_subscribed": False,
    "error_message": None,
}

# state -> monotonic issue time, for OAuth attempts in flight.
_oauth_states: dict = {}


def _update_state(**fields) -> None:
    with _lock:
        _state.update(fields)


# --- what the rest of the feature asks this module ------------------------


def get_access_token() -> Optional[str]:
    """The live token, or None whenever nothing is connected. Everything
    that talks to Instagram reads it from here on every call rather than
    caching it, so a background refresh is picked up immediately and a
    disconnect takes effect on the very next call."""
    with _lock:
        return _access_token


def get_ig_user_id() -> Optional[str]:
    with _lock:
        return _state.get("ig_user_id")


def is_connected() -> bool:
    with _lock:
        return _state.get("stage") == "connected" and _access_token is not None


def self_ids() -> Set[str]:
    """Every id that means "this is our own account, not a customer".

    Load-bearing, not defensive noise: our own public reply to a comment
    arrives back as another `comments` webhook. Without this check the
    handler would answer its own reply, which answers that, forever — a
    genuine infinite loop that also spends the account's send allowance.
    """
    with _lock:
        return {str(value) for value in (_state.get("ig_user_id"), _state.get("account_id")) if value}


def is_self_id(candidate: Optional[str]) -> bool:
    return bool(candidate) and str(candidate) in self_ids()


def get_status() -> dict:
    """What the Connection page renders. Includes the configuration flags so
    the UI can tell the difference between "not connected yet" and "this
    cannot work until .env is filled in", which are very different problems
    with very different fixes."""
    settings = get_settings()
    with _lock:
        status = dict(_state)
    status["oauth_available"] = bool(
        settings.instagram_app_id and settings.instagram_app_secret and settings.instagram_redirect_uri
    )
    status["webhook_secret_configured"] = bool(settings.instagram_app_secret or settings.facebook_app_secret)
    status["webhook_verify_token_configured"] = bool(settings.instagram_webhook_verify_token)
    status["webhook_fields"] = list(WEBHOOK_FIELDS)
    return status


def note_api_error(exc: BaseException, context: str) -> None:
    """Called by every module that makes an Instagram call, on every
    failure.

    Only a genuinely dead token flips the connection to disconnected — see
    instagram_graph_client.is_auth_error for why a rate limit or a missing
    permission deliberately does not. This is what makes the Connection page
    tell the truth within seconds of a token being revoked, instead of the
    feature silently failing every event while still showing "connected".
    """
    if graph.is_auth_error(exc):
        step_logger.error(
            f"Instagram rejected our access token ({context}): {exc} — marking the connection as "
            "disconnected, it needs to be connected again from the Connection page."
        )
        _clear_connection("Instagram ended this connection — please connect again.")
        return
    if graph.is_permission_error(exc):
        step_logger.error(
            f"Instagram refused this action for lack of a permission ({context}): {exc} — reconnecting "
            "will not fix this; the app needs the instagram_business_manage_messages / "
            "instagram_business_manage_comments permissions granted."
        )
        return
    step_logger.warn(f"Instagram API call failed ({context}): {exc}")


# --- connecting -----------------------------------------------------------


def connect_with_token(raw_token: str) -> dict:
    """Connects using a token pasted from the Meta App Dashboard.

    This is the path that needs no OAuth redirect URI and no App Review, so
    it is how the connection is made while developing against an Instagram
    Tester account. The token is validated against /me before anything is
    stored, so a typo or a token for the wrong app fails here with a clear
    message rather than silently "connecting" to nothing.
    """
    token = raw_token.strip()
    if not token:
        return _fail("Paste the access token generated in the Meta App Dashboard.")

    _update_state(stage="connecting", error_message=None)
    try:
        profile = _fetch_profile(token)
    except InstagramApiError as exc:
        return _fail(f"Instagram did not accept that access token: {exc}")

    # Best effort only. A token generated in the dashboard is already
    # long-lived, and asking to exchange one of those is an error — which is
    # a completely normal outcome here, not a failure worth surfacing.
    token, expires_at = _try_exchange_for_long_lived(token)
    return _finish_connect(token, expires_at, profile)


def build_authorize_url() -> Tuple[Optional[str], Optional[str]]:
    """(url, error). The consent screen the operator is sent to for the
    production connection path."""
    settings = get_settings()
    if not settings.instagram_app_id:
        return None, "INSTAGRAM_APP_ID is not set in Backend/.env."
    if not settings.instagram_app_secret:
        return None, "INSTAGRAM_APP_SECRET is not set in Backend/.env."
    if not settings.instagram_redirect_uri:
        return None, (
            "INSTAGRAM_REDIRECT_URI is not set in Backend/.env — set it to this server's public "
            "/api/instagram/oauth/callback URL and add the identical value to the app's OAuth redirect URIs."
        )

    state = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        # Sweep first, so a tab left open for an hour cannot accumulate.
        for key, issued in list(_oauth_states.items()):
            if now - issued > _OAUTH_STATE_TTL_SECONDS:
                _oauth_states.pop(key, None)
        _oauth_states[state] = now

    query = urlencode(
        {
            "client_id": settings.instagram_app_id,
            "redirect_uri": settings.instagram_redirect_uri,
            "response_type": "code",
            "scope": ",".join(OAUTH_SCOPES),
            "state": state,
        }
    )
    return f"{graph.AUTHORIZE_URL}?{query}", None


def complete_oauth(code: str, state: str) -> Tuple[bool, str]:
    """(ok, message). Called by the public OAuth callback route once
    Instagram redirects the browser back with a one-time code."""
    now = time.monotonic()
    with _lock:
        issued = _oauth_states.pop(state, None)
    if issued is None or now - issued > _OAUTH_STATE_TTL_SECONDS:
        return False, "This Instagram login link has expired — please start the connection again."

    settings = get_settings()
    _update_state(stage="connecting", error_message=None)
    try:
        short_lived = _exchange_code_for_token(code, settings)
        token, expires_at = _try_exchange_for_long_lived(short_lived)
        profile = _fetch_profile(token)
    except InstagramApiError as exc:
        _fail(f"Instagram rejected this login: {exc}")
        return False, str(exc)

    _finish_connect(token, expires_at, profile)
    return True, "Connected."


def disconnect() -> dict:
    """Forgets the token locally. Deliberately does NOT call Meta to revoke
    it: the client may well be reconnecting in a moment (a different
    account, a permissions fix), and revoking would force them through the
    whole consent screen again for what is usually a one-line change."""
    _clear_connection(None)
    _persist(None)
    step_logger.info("Instagram disconnected.")
    return get_status()


def _finish_connect(token: str, expires_at: Optional[str], profile: dict) -> dict:
    global _access_token
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        _access_token = token
        _state.update(
            stage="connected",
            username=profile.get("username"),
            ig_user_id=profile.get("ig_user_id"),
            account_id=profile.get("account_id"),
            connected_at=now,
            last_verified_at=now,
            token_expires_at=expires_at,
            error_message=None,
        )
    _persist(
        {
            "access_token": token,
            "token_expires_at": expires_at,
            "username": profile.get("username"),
            "ig_user_id": profile.get("ig_user_id"),
            "account_id": profile.get("account_id"),
            "connected_at": now,
        }
    )
    step_logger.success(f"Instagram connected as @{profile.get('username')} (official API).")
    ensure_webhook_subscription()
    return get_status()


# --- webhook subscription -------------------------------------------------


def ensure_webhook_subscription() -> bool:
    """Subscribes this app to the connected account's comment/message
    events.

    Idempotent and cheap, so it is re-asserted on every connect and on every
    restart rather than assumed. The dashboard's own webhook toggle covers
    the app-level configuration; THIS is the per-account subscription, and
    without it a perfectly configured callback URL simply never receives
    anything — which is the single most common reason an otherwise correct
    Instagram integration appears to do nothing at all.
    """
    token = get_access_token()
    if token is None:
        return False
    try:
        graph.graph_post(
            "me/subscribed_apps",
            token=token,
            params={"subscribed_fields": ",".join(WEBHOOK_FIELDS)},
        )
    except InstagramApiError as exc:
        _update_state(webhook_subscribed=False)
        note_api_error(exc, "subscribing to webhooks")
        return False
    _update_state(webhook_subscribed=True)
    step_logger.success(
        f"Instagram webhooks subscribed for @{get_status().get('username')} "
        f"({', '.join(WEBHOOK_FIELDS)}) — comments and DMs will now be pushed to this server."
    )
    return True


# --- startup restore and background maintenance ---------------------------


def load_from_database() -> None:
    """Called once at startup (see main.py's lifespan). Restores the stored
    token WITHOUT calling Instagram — deliberately, because startup must not
    block on a third-party network call. The very first pass of the
    maintenance thread below verifies it moments later and corrects the
    status if it has gone bad."""
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if not stored or not stored.get("access_token"):
        return

    global _access_token
    with _lock:
        _access_token = stored["access_token"]
        _state.update(
            stage="connected",
            username=stored.get("username"),
            ig_user_id=stored.get("ig_user_id"),
            account_id=stored.get("account_id"),
            connected_at=stored.get("connected_at"),
            token_expires_at=stored.get("token_expires_at"),
            last_verified_at=None,
            webhook_subscribed=False,
            error_message=None,
        )
    step_logger.success(
        f"Instagram connection restored for @{stored.get('username')} — verifying it in the background."
    )


def start_background_maintenance() -> None:
    """One daemon thread for the life of the process, and it is genuinely
    idle almost all of it: it wakes every six hours, and when nothing is
    connected it does nothing at all. This is the ONLY recurring Instagram
    work left in this application — the old 8-second comment/DM poll is gone
    entirely, replaced by webhooks."""
    thread = threading.Thread(target=_maintenance_loop, name="instagram-maintenance", daemon=True)
    thread.start()


def _maintenance_loop() -> None:
    # First pass immediately: this is what verifies a token restored at
    # startup and re-asserts the webhook subscription after a redeploy.
    while True:
        try:
            _maintenance_pass()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Instagram maintenance pass failed ({type(exc).__name__}): {exc!r}")
        time.sleep(_MAINTENANCE_INTERVAL_SECONDS)


def _maintenance_pass() -> None:
    token = get_access_token()
    if token is None:
        return

    if _should_refresh():
        token = _refresh_token(token) or token

    try:
        profile = _fetch_profile(token)
    except InstagramApiError as exc:
        note_api_error(exc, "verifying the connection")
        return

    _update_state(
        last_verified_at=datetime.now(timezone.utc).isoformat(),
        username=profile.get("username") or get_status().get("username"),
        ig_user_id=profile.get("ig_user_id") or get_status().get("ig_user_id"),
        account_id=profile.get("account_id") or get_status().get("account_id"),
    )
    if not get_status().get("webhook_subscribed"):
        ensure_webhook_subscription()


def _should_refresh() -> bool:
    expires_at = get_status().get("token_expires_at")
    if not expires_at:
        # Unknown expiry (a token pasted from the dashboard that could not be
        # exchanged). Attempting a refresh is harmless — it either succeeds
        # and teaches us the real expiry, or it is refused and changes
        # nothing.
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry - datetime.now(timezone.utc) <= _REFRESH_WHEN_WITHIN


def _refresh_token(token: str) -> Optional[str]:
    global _access_token
    try:
        body = graph.graph_get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
    except InstagramApiError as exc:
        # NOT treated as a dead session unless Meta actually says so: a
        # long-lived token younger than 24 hours simply cannot be refreshed
        # yet, and that refusal must not disconnect a working connection.
        if graph.is_auth_error(exc):
            note_api_error(exc, "refreshing the access token")
        else:
            step_logger.info(f"Instagram access token was not refreshed this time ({exc}) — will retry later.")
        return None

    new_token = (body or {}).get("access_token")
    if not new_token:
        return None
    expires_at = _expiry_from(body)
    with _lock:
        _access_token = new_token
        _state["token_expires_at"] = expires_at
        # Rebuilt from live state rather than re-read from the database:
        # this row is only ever written by this module, so memory is already
        # the authority, and a read here would be one more Neon query for
        # nothing.
        payload = {
            "access_token": new_token,
            "token_expires_at": expires_at,
            "username": _state.get("username"),
            "ig_user_id": _state.get("ig_user_id"),
            "account_id": _state.get("account_id"),
            "connected_at": _state.get("connected_at"),
        }
    _persist(payload)
    step_logger.success(f"Instagram access token refreshed (valid until {expires_at}).")
    return new_token


# --- small helpers --------------------------------------------------------


def _fetch_profile(token: str) -> dict:
    """The cheapest authenticated call there is, and the only one used to
    answer "is this token good?". `user_id` is the Instagram professional
    account id; `id` is the app-scoped one — see _state's comments for why
    both are kept."""
    body = graph.graph_get("me", token=token, params={"fields": "user_id,username"}) or {}
    return {
        "username": body.get("username"),
        "ig_user_id": str(body["user_id"]) if body.get("user_id") else None,
        "account_id": str(body["id"]) if body.get("id") else None,
    }


def _exchange_code_for_token(code: str, settings) -> str:
    # form_body, not params: this is an OAuth token endpoint, which takes its
    # parameters as a form-encoded body (see graph_post's docstring). It is
    # the only call in this application that does.
    body = graph.graph_post(
        graph.TOKEN_EXCHANGE_URL,
        form_body={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": settings.instagram_redirect_uri,
            "code": code,
        },
    )
    # Meta returns either a flat object or a one-element `data` list here,
    # depending on how recently the app was created. Both shapes are live in
    # the wild, so both are accepted rather than betting on one.
    if isinstance(body, dict):
        if body.get("access_token"):
            return str(body["access_token"])
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("access_token"):
            return str(data[0]["access_token"])
    raise InstagramApiError("Instagram did not return an access token for this login.")


def _try_exchange_for_long_lived(token: str) -> Tuple[str, Optional[str]]:
    """(token, expires_at_iso). Returns the token unchanged when the
    exchange is refused, which is the normal outcome for a token that is
    already long-lived."""
    settings = get_settings()
    if not settings.instagram_app_secret:
        return token, None
    try:
        body = graph.graph_get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_app_secret,
                "access_token": token,
            },
        )
    except InstagramApiError:
        return token, None
    new_token = (body or {}).get("access_token")
    if not new_token:
        return token, None
    return str(new_token), _expiry_from(body)


def _expiry_from(body: Optional[dict]) -> Optional[str]:
    seconds = (body or {}).get("expires_in")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _clear_connection(error_message: Optional[str]) -> None:
    global _access_token
    with _lock:
        _access_token = None
        _state.update(
            stage="error" if error_message else "disconnected",
            username=None,
            ig_user_id=None,
            account_id=None,
            connected_at=None,
            last_verified_at=None,
            token_expires_at=None,
            webhook_subscribed=False,
            error_message=error_message,
        )


def _persist(payload: Optional[dict]) -> None:
    if not is_database_configured():
        return
    try:
        settings_repository.set_value(_SETTINGS_KEY, payload or {})
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Could not save the Instagram connection ({type(exc).__name__}): {exc!r}")


def _fail(message: str) -> dict:
    step_logger.error(f"Instagram connect failed: {message}")
    _update_state(stage="error", error_message=message)
    return get_status()
