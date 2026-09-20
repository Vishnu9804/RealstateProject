"""The single place this application talks HTTP to Meta's Instagram
Platform API — every other Instagram module goes through here.

Why one module rather than a `requests.get` at each call site:

  - ONE connection pool. Every call goes to graph.instagram.com, so a
    single keep-alive session means one TLS handshake for the life of the
    process instead of one per comment reply. On a small Railway instance
    a TLS handshake is by far the most expensive part of a request that
    only carries a few hundred bytes of JSON.
  - ONE error shape. Meta answers failures with a JSON `error` object
    (message / type / code / error_subcode) and a non-2xx status. Parsing
    that in one place is what lets callers ask the only two questions they
    actually care about — "is our access token dead?" (is_auth_error) and
    "is this Meta refusing this particular action?" — instead of every
    caller re-deriving them from a status code.
  - ONE timeout. A hung socket must never hold a webhook worker thread
    forever; the whole point of the webhook design is that work is short
    and the pool is tiny.

Deliberately NOT here: the access token. This module is given one per
call. That keeps it importable from instagram_connection_service (which
owns the token) without a circular import, and it means a token-refresh
race can never leave a stale token captured inside this module.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

from Config.settings import get_settings

# Instagram API with Instagram Login is served from graph.instagram.com —
# NOT graph.facebook.com, which is the Facebook-Login flavour of the same
# product and rejects these tokens.
_BASE_HOST = "https://graph.instagram.com"

# OAuth lives on two different hosts, which is a genuine quirk of this API
# rather than an oversight: the authorization dialog and the code->token
# exchange are on instagram.com / api.instagram.com, while everything
# afterwards (including the long-lived-token exchange and refresh) is on
# graph.instagram.com.
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_EXCHANGE_URL = "https://api.instagram.com/oauth/access_token"

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


class InstagramApiError(RuntimeError):
    """A refusal from Meta, with the parts a caller can actually act on.

    `code` 190 (and the OAuthException type) is the one that matters most:
    it means the access token is no longer usable, which is the difference
    between "retry this later" and "the Connection page must say
    disconnected and ask for a reconnect".
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        error_type: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.http_status = http_status

    def __str__(self) -> str:  # noqa: D105
        parts = [super().__str__()]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.subcode is not None:
            parts.append(f"subcode={self.subcode}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        return " ".join(parts) if len(parts) == 1 else f"{parts[0]} ({', '.join(parts[1:])})"


def is_auth_error(exc: BaseException) -> bool:
    """True only for "this access token will never work again" — an expired,
    revoked or wrong-account token. Everything else (a rate limit, a
    permission the app doesn't hold, a transient 500) is NOT this, because
    treating those as a dead session would disconnect a perfectly good
    connection over one bad minute."""
    if not isinstance(exc, InstagramApiError):
        return False
    if exc.code in (190, 102, 463, 467):
        return True
    return exc.error_type == "OAuthException" and exc.code is None and exc.http_status == 401


def is_permission_error(exc: BaseException) -> bool:
    """A token that works but isn't allowed to do this — a missing scope, or
    an app still waiting on App Review. Worth logging differently from a
    dead session: reconnecting does not fix it, granting the permission
    does."""
    return isinstance(exc, InstagramApiError) and exc.code in (10, 200, 3)


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            session = requests.Session()
            # Small on purpose: this app makes a handful of calls per real
            # Instagram event, never a burst of hundreds. A large pool would
            # only reserve sockets that are never used.
            adapter = HTTPAdapter(pool_connections=2, pool_maxsize=8, max_retries=0)
            session.mount("https://", adapter)
            _session = session
        return _session


def _version() -> str:
    return get_settings().instagram_graph_version.strip() or "v23.0"


def _timeout() -> int:
    return get_settings().instagram_api_timeout_seconds


def _url(path: str) -> str:
    """Absolute URLs pass through untouched (the two OAuth hosts above);
    anything else is a Graph path like "me/messages" or a bare node id."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{_BASE_HOST}/{_version()}/{path.lstrip('/')}"


def _parse(response: requests.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        raise InstagramApiError(
            str(error.get("error_user_msg") or error.get("message") or "Instagram refused the request."),
            code=_as_int(error.get("code")),
            subcode=_as_int(error.get("error_subcode")),
            error_type=error.get("type"),
            http_status=response.status_code,
        )

    if not response.ok:
        raise InstagramApiError(
            f"Instagram returned HTTP {response.status_code}.",
            http_status=response.status_code,
        )
    return body


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def graph_get(path: str, *, token: Optional[str] = None, params: Optional[dict] = None) -> Any:
    """A read, retried ONCE on a transient failure.

    Reads are retried and writes are not, and the asymmetry is the whole
    point: a GET is idempotent, so repeating one can only cost a little
    latency. Repeating a POST to the Send API, on the other hand, would send
    a second message to a real person if the first actually succeeded and
    only its response was lost — Meta does not de-duplicate, so a "safe"
    retry there is the one thing worse than dropping the event.

    The retry matters most for one call in particular: resolving a media id
    to its permalink (instagram_reel_matcher). If that read fails, the
    comment that triggered it cannot be matched to a property and is simply
    never answered — Meta will not re-deliver a notification this server
    already acknowledged.
    """
    query = dict(params or {})
    if token:
        query["access_token"] = token
    url = _url(path)

    last_error: Optional[InstagramApiError] = None
    for attempt in range(2):
        try:
            response = _get_session().get(url, params=query, timeout=_timeout())
        except requests.RequestException as exc:
            last_error = InstagramApiError(f"Could not reach Instagram: {exc}")
        else:
            try:
                return _parse(response)
            except InstagramApiError as exc:
                # A refusal Meta actually reasoned about (a bad token, a
                # missing object, a permission) is a final answer and is
                # raised straight away — retrying it would just ask the same
                # question twice. Only a server-side wobble or a throttle is
                # worth a second go.
                if not _is_transient(exc):
                    raise
                last_error = exc
        if attempt == 0:
            time.sleep(_RETRY_DELAY_SECONDS)
    raise last_error if last_error is not None else InstagramApiError("Instagram request failed.")


# Codes Meta uses for "too many requests, slow down" / "try again". Short
# single retry only — a webhook worker must not sit here.
_TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 341, 613})
_RETRY_DELAY_SECONDS = 1.5


def _is_transient(exc: InstagramApiError) -> bool:
    if exc.http_status is not None and exc.http_status >= 500:
        return True
    if exc.code is None:
        # No structured error at all — a proxy/gateway hiccup rather than a
        # decision Meta made.
        return exc.http_status is None or exc.http_status >= 500
    return exc.code in _TRANSIENT_CODES


def graph_post(
    path: str,
    *,
    token: Optional[str] = None,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    form_body: Optional[dict] = None,
) -> Any:
    """POST, with the token in the QUERY STRING by default.

    That is not a stylistic choice: the Send API's body is the `recipient` /
    `message` JSON document and has no room for an access_token beside them,
    while the comment-reply endpoint takes a plain `message` parameter and no
    JSON document at all. A token in the query string is the one form both
    accept, and is what Meta's own examples use.

    `form_body` is the exception, and it exists for exactly one endpoint:
    api.instagram.com/oauth/access_token, which is an OAuth token endpoint
    rather than a Graph node and expects its parameters as an
    application/x-www-form-urlencoded body. Sending those in the query string
    is refused by some deployments of it, so that one call passes them here.

    Never retried — see graph_get's docstring for why repeating a write
    against the Send API is worse than losing it.
    """
    query = dict(params or {})
    if token:
        query["access_token"] = token
    try:
        response = _get_session().post(
            _url(path), params=query, json=json_body, data=form_body, timeout=_timeout()
        )
    except requests.RequestException as exc:
        raise InstagramApiError(f"Could not reach Instagram: {exc}") from exc
    return _parse(response)
