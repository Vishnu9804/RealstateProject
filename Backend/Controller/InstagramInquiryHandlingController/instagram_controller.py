"""HTTP routes for the Instagram connection — the Instagram-tab equivalent
of Controller/WhatsAppDataFetchingController/whatsapp_controller.py. Thin by
design; all logic lives in Service/InstagramInquiryHandlingService/
instagram_connection_service.py.

Plain JSON in, JSON out — unlike a WhatsApp-style QR pairing or an OAuth
flow, logging in is just POSTing a username/password, and the resulting
2FA/challenge code prompt is just another POST once the client has it. The
frontend polls /status to know which state it's in, same as it already
polls the WhatsApp connection's status.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from Service.InstagramInquiryHandlingService import instagram_connection_service

router = APIRouter(prefix="/instagram", tags=["instagram"])


class InstagramConnectRequest(BaseModel):
    username: str
    password: str


class InstagramVerifyRequest(BaseModel):
    code: str


@router.get("/status")
def get_status() -> dict:
    return instagram_connection_service.get_status()


@router.post("/connect")
def connect(body: InstagramConnectRequest) -> dict:
    return instagram_connection_service.connect(body.username.strip(), body.password)


@router.post("/verify")
def verify(body: InstagramVerifyRequest) -> dict:
    return instagram_connection_service.submit_verification_code(body.code)


@router.post("/retry-approval")
def retry_approval() -> dict:
    """For the "approve this in the real Instagram app" checkpoint only —
    called once the client says they've done that."""
    return instagram_connection_service.retry_after_manual_approval()


@router.post("/start-over")
def start_over() -> dict:
    """Abandons whatever attempt is in progress and returns to the plain
    login form."""
    return instagram_connection_service.start_over()


@router.post("/disconnect")
def disconnect() -> dict:
    return instagram_connection_service.disconnect()
