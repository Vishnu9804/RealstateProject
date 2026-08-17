"""HTTP routes for the WhatsApp feature. Thin by design — all logic lives in
Service/whatsapp_service.py; this module only translates HTTP <-> Service.
"""

from fastapi import APIRouter

from Model.group import WhatsAppGroup
from Model.personal_chat import WhatsAppPersonalChat
from Model.whatsapp_message import WhatsAppChatMessage
from Service import whatsapp_service

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/status")
def get_status() -> dict:
    return whatsapp_service.get_status()


@router.get("/groups", response_model=list[WhatsAppGroup])
def get_groups() -> list[WhatsAppGroup]:
    return whatsapp_service.get_joined_groups()


@router.get("/groups/monitored", response_model=list[WhatsAppGroup])
def get_monitored_groups() -> list[WhatsAppGroup]:
    return whatsapp_service.get_monitored_groups()


@router.get("/personal-chats/monitored", response_model=list[WhatsAppPersonalChat])
def get_monitored_personal_chats() -> list[WhatsAppPersonalChat]:
    return whatsapp_service.get_monitored_personal_chats()


@router.get("/messages", response_model=list[WhatsAppChatMessage])
def get_messages(limit: int = 100) -> list[WhatsAppChatMessage]:
    return whatsapp_service.get_messages(limit=limit)
