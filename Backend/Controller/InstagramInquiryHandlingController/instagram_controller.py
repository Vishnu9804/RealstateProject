"""HTTP routes for the Instagram connection — the Instagram-tab equivalent
of Controller/WhatsAppDataFetchingController/whatsapp_controller.py. Thin by
design; all logic lives in Service/InstagramInquiryHandlingService/
instagram_connection_service.py.

Everything here requires a logged-in operator (see main.py). The routes Meta
itself calls — the webhook handshake, the webhook deliveries, and the OAuth
redirect the browser comes back on — cannot require a login and therefore
live in instagram_webhook_controller.py instead.

Two ways to connect, both ending in the same connected state:

  POST /instagram/connect-token   paste a token generated in the Meta App
                                  Dashboard next to an Instagram Tester
                                  account. Needs no redirect URI and no App
                                  Review, so it is the fastest way to get a
                                  test account working end to end.
  GET  /instagram/oauth/url       the production path: returns Instagram's
                                  own consent-screen URL for the browser to
                                  visit. Only available once INSTAGRAM_APP_ID,
                                  INSTAGRAM_APP_SECRET and
                                  INSTAGRAM_REDIRECT_URI are configured.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from Config.settings import get_settings
from Service.AuthManagementService.auth_dependencies import require_admin
from Service.InstagramInquiryHandlingService import instagram_connection_service

router = APIRouter(prefix="/instagram", tags=["instagram"])


class InstagramTokenRequest(BaseModel):
    access_token: str


@router.get("/status")
def get_status() -> dict:
    return instagram_connection_service.get_status()


@router.get("/setup")
def get_setup(request: Request) -> dict:
    """What to paste where in the Meta App Dashboard, computed from this
    server's own public URL rather than written down by hand.

    The address is taken from the request itself, which behind ngrok or a
    Railway domain is exactly the public address Meta must be given — so the
    operator can copy the callback URL instead of assembling it and getting
    the path wrong (the single most common setup mistake: pasting the bare
    domain without /api/instagram/webhook).

    The forwarded headers are read explicitly rather than relying on
    uvicorn's --proxy-headers, and the SCHEME matters more than the host
    here: a tunnel terminates TLS at its own edge and forwards plain HTTP,
    so request.url.scheme is "http" even though the public URL is https —
    and Meta rejects an http callback URL outright.
    """
    settings = get_settings()
    base = settings.instagram_public_base_url.strip().rstrip("/")
    if not base:
        forwarded_host = request.headers.get("x-forwarded-host")
        host = (forwarded_host or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
        scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
        # A public tunnel/host is always https in practice; only a direct
        # localhost/LAN hit is genuinely http, and Meta is never pointed at
        # one — which is exactly the case INSTAGRAM_PUBLIC_BASE_URL exists
        # for, since during development the browser reaches this API on the
        # LAN while Meta reaches it through a tunnel.
        if "localhost" not in host and not host.startswith("127.") and not host.startswith("192.168."):
            scheme = "https"
        base = f"{scheme}://{host}"
    return {
        "callback_url": f"{base}/api/instagram/webhook",
        "oauth_redirect_uri": f"{base}/api/instagram/oauth/callback",
        "configured_redirect_uri": settings.instagram_redirect_uri or None,
        "verify_token_configured": bool(settings.instagram_webhook_verify_token),
        "app_secret_configured": bool(settings.instagram_app_secret or settings.facebook_app_secret),
        "app_id_configured": bool(settings.instagram_app_id),
        "subscribed_fields": list(instagram_connection_service.WEBHOOK_FIELDS),
    }


@router.post("/connect-token")
def connect_token(body: InstagramTokenRequest) -> dict:
    return instagram_connection_service.connect_with_token(body.access_token)


@router.get("/oauth/url")
def oauth_url() -> dict:
    url, error = instagram_connection_service.build_authorize_url()
    if url is None:
        raise HTTPException(status_code=400, detail=error or "Instagram login is not configured.")
    return {"url": url}


@router.post("/resubscribe")
def resubscribe() -> dict:
    """Re-asserts the webhook subscription for the connected account.

    Idempotent, and the first thing to try when comments and DMs are not
    arriving despite the connection showing as healthy — the subscription
    lives on Meta's side and is the piece most likely to be missing.
    """
    instagram_connection_service.ensure_webhook_subscription()
    return instagram_connection_service.get_status()


@router.post("/disconnect", dependencies=[Depends(require_admin)])
def disconnect() -> dict:
    return instagram_connection_service.disconnect()
