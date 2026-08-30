"""HTTP routes for the whatsappInquiryHandling feature. Thin by design — all
logic lives in Service/WhatsAppInquiryHandlingService/whatsapp_inquiry_service.py;
this module only translates HTTP <-> Service.
"""

from fastapi import APIRouter, HTTPException, Response

from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.WhatsAppInquiryHandlingService import whatsapp_inquiry_service

router = APIRouter(prefix="/whatsapp-inquiry", tags=["whatsapp-inquiry"])


@router.get("/status")
def get_status() -> dict:
    return whatsapp_inquiry_service.get_status()


@router.get("/qr")
def get_qr_code() -> Response:
    """Latest WhatsApp pairing QR code as a PNG image for the inquiry-handling
    connection — poll this while status is "waiting_for_qr_scan" and render it
    directly (e.g. <img src="/api/whatsapp-inquiry/qr">). This is a separate
    linked device from /api/whatsapp/qr (whatsappDataFetching); pairing one
    does not affect the other. 404 whenever there's nothing to scan right now
    (not generated yet, already paired, or the code was superseded)."""
    png_bytes = whatsapp_inquiry_service.get_qr_code()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No QR code available right now.")
    return Response(content=png_bytes, media_type="image/png")


@router.get("/messages", response_model=list[InquiryChatMessage])
def get_messages(limit: int = 100) -> list[InquiryChatMessage]:
    return whatsapp_inquiry_service.get_messages(limit=limit)
