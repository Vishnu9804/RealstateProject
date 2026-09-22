"""The two routes Meta itself calls, plus the browser redirect that ends the
Instagram OAuth round trip.

Kept in a router of its own, separate from instagram_controller.py, for one
reason: these three routes must be reachable WITHOUT a login. Meta's servers
have no account here, and the OAuth callback is a top-level browser
navigation arriving from instagram.com with no token attached. Every other
Instagram route is part of the internal ops tool and stays behind
`Depends(get_current_user)` — see main.py, where this router is included
without that dependency and the other one with it.

Authentication is therefore not "none", it is different in kind:

  GET  /api/instagram/webhook   proves it is Meta by echoing back a challenge
                                only if the caller knows the verify token we
                                configured in the App Dashboard.
  POST /api/instagram/webhook   proves it is Meta by an HMAC-SHA256 of the
                                EXACT bytes of the body, keyed on the app
                                secret (X-Hub-Signature-256).
  GET  /api/instagram/oauth/callback
                                proves the redirect belongs to a login this
                                server actually started, via a one-time
                                `state` issued by instagram_connection_service.

The POST handler does as little as possible before answering: verify, hand
the payload to a worker, return 200. Meta re-delivers anything it does not
get a prompt 200 for, so any work done inline here would eventually show up
as duplicate events rather than as a slow response.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from Config.settings import get_settings
from Middleware import step_logger
from Service.InstagramInquiryHandlingService import instagram_connection_service, instagram_event_service

router = APIRouter(prefix="/instagram", tags=["instagram-webhook"])


@router.get("/webhook")
def verify_webhook(request: Request) -> Response:
    """Meta's one-time handshake when the callback URL is saved in the App
    Dashboard, and again whenever it is re-verified.

    Returns the challenge as PLAIN TEXT, not JSON. Meta compares the body
    byte-for-byte against the challenge it sent, so a JSON-quoted version of
    the same number fails verification with a message that gives no hint why.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    expected = get_settings().instagram_webhook_verify_token.strip()
    if not expected:
        step_logger.error(
            "Instagram webhook verification was attempted but INSTAGRAM_WEBHOOK_VERIFY_TOKEN is not set in "
            "Backend/.env — set it to the same string typed into the App Dashboard's 'Verify token' box."
        )
        return PlainTextResponse("Verify token is not configured on this server.", status_code=500)

    if mode == "subscribe" and token == expected and challenge is not None:
        step_logger.success("Instagram webhook callback URL verified by Meta.")
        return PlainTextResponse(challenge, status_code=200)

    step_logger.warn(
        "Rejected an Instagram webhook verification attempt: the verify token did not match the one in "
        "Backend/.env. Check that the App Dashboard's 'Verify token' box has exactly the same value."
    )
    return PlainTextResponse("Verification failed.", status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request) -> Response:
    """A batch of events. Always answers 200 once the sender is
    authenticated, even if the payload turns out to be something this app
    does not act on — a non-200 tells Meta to re-deliver, and re-delivering
    an event we deliberately ignored would be an endless retry loop."""
    body = await request.body()

    if not _signature_ok(body, request.headers.get("x-hub-signature-256")):
        return PlainTextResponse("Invalid signature.", status_code=403)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        step_logger.warn("Discarded an Instagram webhook delivery whose body was not valid JSON.")
        return PlainTextResponse("OK", status_code=200)

    # Meta uses one webhook endpoint per app across products; anything that
    # is not an Instagram notification is acknowledged and dropped rather
    # than misread.
    if isinstance(payload, dict) and payload.get("object") == "instagram":
        step_logger.info(f"Instagram webhook received: {_summarize(payload)}")
        instagram_event_service.enqueue_webhook(payload)
    else:
        step_logger.warn(
            f"Instagram webhook delivery had an unexpected 'object' value: {(payload or {}).get('object')!r} "
            "— acknowledged and dropped."
        )

    return PlainTextResponse("OK", status_code=200)


def _summarize(payload: dict) -> str:
    """One line naming what a delivery actually contains — comments,
    messaging events, or neither — printed BEFORE anything tries to match it
    to a property.

    This exists because "nothing replied" and "nothing arrived" look
    identical from the outside otherwise: every downstream step (matching a
    reel, checking the daily allowance) is deliberately silent when it finds
    nothing to do, which is the right call for cost but leaves this line as
    the only place in the whole pipeline that proves Meta actually reached
    this server at all. If a comment or a shared reel produces no such line
    in the terminal, the fault is upstream of this application entirely —
    most commonly the sending Instagram account not having accepted an
    Instagram Tester role on this app, which is what Meta requires before it
    will deliver ANY webhook triggered by that account while the app is
    still in Development mode.

    Counts BOTH shapes a "messages" event can arrive in — entry.changes[]
    with field == "messages" (what "API setup with Instagram business
    login" actually sends, confirmed against the App Dashboard's own "Send
    to My Server" sample) and entry.messaging[] (the Messenger-Platform
    shape, used by Facebook Login for Business). Counting only one of them
    is exactly what made an earlier version of this line lie: it reported
    "0 messaging event(s)" for a delivery that, moments later, this
    application also failed to act on for the very same reason — the
    dispatcher and this summary must always agree on what a "messages" event
    looks like, so the two are handled by the same enumeration below.
    """
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return "no entries"
    comments = 0
    messages = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
        fields = [c.get("field") for c in changes if isinstance(c, dict)]
        if entry.get("field"):
            fields.append(entry.get("field"))
        comments += fields.count("comments")
        messages += fields.count("messages")
        messaging = entry.get("messaging")
        if isinstance(messaging, list):
            messages += len(messaging)
    return f"{comments} comment event(s), {messages} messaging event(s)"


def _signature_ok(body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 over the RAW request bytes.

    It has to be the raw bytes, not a re-serialised copy of the parsed JSON:
    key order, spacing and unicode escaping would all differ, and every
    delivery would be rejected.

    Both configured secrets are tried. Meta has two on a combined app (the
    Facebook app secret and the Instagram one) and which of them signs a
    delivery depends on how the app was created, so accepting either removes
    a whole class of "every event is rejected" confusion without weakening
    anything — an attacker would still have to know one of them.
    """
    settings = get_settings()
    secrets_to_try = [s for s in (settings.instagram_app_secret, settings.facebook_app_secret) if s]

    if not secrets_to_try:
        # Nothing to verify against. Refusing here would make the feature
        # impossible to bring up at all, so this proceeds and says loudly
        # why that is not acceptable for a hosted deployment.
        step_logger.warn(
            "An Instagram webhook was accepted WITHOUT verifying its signature, because neither "
            "INSTAGRAM_APP_SECRET nor FACEBOOK_APP_SECRET is set in Backend/.env. Set INSTAGRAM_APP_SECRET "
            "before hosting this — until then anyone who knows this URL can post fake events to it."
        )
        return True

    if not header or not header.startswith("sha256="):
        step_logger.warn("Rejected an Instagram webhook delivery with no X-Hub-Signature-256 header.")
        return False
    provided = header.split("=", 1)[1].strip()

    for secret in secrets_to_try:
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, provided):
            return True

    step_logger.warn(
        "Rejected an Instagram webhook delivery whose signature did not match. If events are not arriving, "
        "check that INSTAGRAM_APP_SECRET in Backend/.env is the secret shown on the app's Instagram "
        "API-setup screen (and set FACEBOOK_APP_SECRET to the App-settings -> Basic one as well)."
    )
    return False


@router.get("/oauth/callback")
def oauth_callback(request: Request) -> Response:
    """Where Instagram sends the browser after the consent screen.

    Always ends in a redirect back to the internal tool's Connection page
    with `?instagram=connected` or `?instagram=error&message=...`, which
    Frontend/src/pages/ConnectionPage.tsx already turns into a toast — the
    person who clicked Connect never sees a bare API response.
    """
    params = request.query_params
    error = params.get("error_description") or params.get("error")
    if error:
        return _back_to_frontend(ok=False, message=error)

    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return _back_to_frontend(ok=False, message="Instagram did not send back a login code.")

    ok, message = instagram_connection_service.complete_oauth(code, state)
    return _back_to_frontend(ok=ok, message=message)


def _back_to_frontend(*, ok: bool, message: str) -> RedirectResponse:
    base = get_settings().frontend_base_url.rstrip("/")
    query = urlencode({"instagram": "connected"} if ok else {"instagram": "error", "message": message})
    return RedirectResponse(url=f"{base}/?{query}", status_code=303)
