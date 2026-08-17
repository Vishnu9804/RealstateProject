"""HTTP routes for the WhatsApp feature. Thin by design — all logic lives in
Service/whatsapp_service.py; this module only translates HTTP <-> Service.
"""

from fastapi import APIRouter, HTTPException, Response

from Model.group import WhatsAppGroup
from Model.monitoring_selection import MonitoringSelectionRequest
from Model.personal_chat import WhatsAppPersonalChat
from Model.whatsapp_message import WhatsAppChatMessage
from Service import whatsapp_service

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/status")
def get_status() -> dict:
    return whatsapp_service.get_status()


@router.get("/qr")
def get_qr_code() -> Response:
    """Latest WhatsApp pairing QR code as a PNG image — poll this while
    status is "waiting_for_qr_scan" and render it directly (e.g. <img
    src="/api/whatsapp/qr">). 404 whenever there's nothing to scan right
    now (not generated yet, already paired, or the code was superseded)."""
    png_bytes = whatsapp_service.get_qr_code()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No QR code available right now.")
    return Response(content=png_bytes, media_type="image/png")


@router.get("/groups", response_model=list[WhatsAppGroup])
def get_groups() -> list[WhatsAppGroup]:
    return whatsapp_service.get_joined_groups()


@router.get("/groups/monitored", response_model=list[WhatsAppGroup])
def get_monitored_groups() -> list[WhatsAppGroup]:
    return whatsapp_service.get_monitored_groups()


@router.get("/personal-chats/monitored", response_model=list[WhatsAppPersonalChat])
def get_monitored_personal_chats() -> list[WhatsAppPersonalChat]:
    return whatsapp_service.get_monitored_personal_chats()


@router.post("/monitoring-selection")
def submit_monitoring_selection(selection: MonitoringSelectionRequest) -> dict:
    """Submits which groups/personal numbers to monitor. Safe to call again
    later — each call fully replaces the previous selection, so the UI also
    uses this to change what's monitored without restarting the backend."""
    try:
        whatsapp_service.submit_monitoring_selection(
            selection.group_jids, selection.personal_phone_numbers
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "monitored_groups": whatsapp_service.get_monitored_groups(),
        "monitored_personal_chats": whatsapp_service.get_monitored_personal_chats(),
    }


@router.get("/messages", response_model=list[WhatsAppChatMessage])
def get_messages(limit: int = 100) -> list[WhatsAppChatMessage]:
    return whatsapp_service.get_messages(limit=limit)
