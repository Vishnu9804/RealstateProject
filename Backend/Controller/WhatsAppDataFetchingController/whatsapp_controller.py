"""HTTP routes for the property pipeline's message intake. Thin by design —
all logic lives in Service/WhatsAppDataFetchingService/whatsapp_service.py;
this module only translates HTTP <-> Service. Connection/pairing routes
(multiple numbers, QR, roles, group selection) live in
whatsapp_connections_controller.py.
"""

from fastapi import APIRouter

from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import whatsapp_service

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/status")
def get_status() -> dict:
    return whatsapp_service.get_status()


@router.get("/messages", response_model=list[WhatsAppChatMessage])
def get_messages(limit: int = 100) -> list[WhatsAppChatMessage]:
    return whatsapp_service.get_messages(limit=limit)


@router.get("/messages/qualified", response_model=list[WhatsAppChatMessage])
def get_qualified_messages(limit: int = 100) -> list[WhatsAppChatMessage]:
    """Messages that passed the area-keyword filter — see
    /api/area-filter/keywords to configure which areas qualify a message."""
    return whatsapp_service.get_qualified_messages(limit=limit)
