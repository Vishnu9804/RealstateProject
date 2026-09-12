"""HTTP routes for the redesigned Connection page: linking multiple WhatsApp
numbers, assigning each one the Property and/or Inquiry role, and picking
which groups/personal numbers a Property-role connection feeds into the
property pipeline. All QR/pairing state lives here now — the Inquiries page
no longer has any connection UI of its own.

Thin by design — all logic lives in Service/WhatsAppDataFetchingService/
whatsapp_connection_manager.py; this module only translates HTTP <-> Service.
"""

from fastapi import APIRouter, HTTPException, Response

from Model.WhatsAppDataFetchingModel.whatsapp_connection import (
    PropertyRequirementSelectionRequest,
    UpdateRolesRequest,
    WhatsAppConnectionView,
)
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager

router = APIRouter(prefix="/whatsapp/connections", tags=["whatsapp-connections"])


@router.get("", response_model=list[WhatsAppConnectionView])
def list_connections() -> list[WhatsAppConnectionView]:
    return whatsapp_connection_manager.list_connections()


@router.get("/qr")
def get_pending_qr() -> Response:
    """QR code for the number currently being onboarded, if any — see
    POST .../onboard to start one. Poll this while a pending connection
    exists and render it directly (e.g. <img src="/api/whatsapp/connections/qr">).
    404 whenever nothing is being onboarded right now, or the code hasn't
    been generated yet."""
    png_bytes = whatsapp_connection_manager.get_pending_qr()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No QR code available right now.")
    return Response(content=png_bytes, media_type="image/png")


@router.post("/onboard", response_model=WhatsAppConnectionView)
def start_onboarding() -> WhatsAppConnectionView:
    """Starts linking a new number — call this when the operator taps "Add
    a number", then poll GET / and GET /qr. Safe to call again while one is
    already in progress; it just returns that same one instead of starting
    a duplicate."""
    return whatsapp_connection_manager.start_onboarding()


@router.delete("/onboard", status_code=204)
def cancel_onboarding() -> None:
    """Backs out of an in-progress onboarding before it's scanned. No-op if
    nothing is currently pending."""
    whatsapp_connection_manager.cancel_onboarding()


@router.patch("/{connection_id}/roles", response_model=WhatsAppConnectionView)
def update_roles(connection_id: str, body: UpdateRolesRequest) -> WhatsAppConnectionView:
    """Fully replaces this connection's roles. Dropping the Property role
    clears whatever Property group/personal selection it had — it has to be
    re-picked if Property is turned back on later, since the groups it used
    to claim may no longer be what's wanted."""
    try:
        return whatsapp_connection_manager.set_roles(connection_id, [r.value for r in body.roles])
    except KeyError:
        raise HTTPException(status_code=404, detail="No such connection.")


@router.post("/{connection_id}/property-requirement-selection", response_model=WhatsAppConnectionView)
def update_property_requirement_selection(
    connection_id: str, body: PropertyRequirementSelectionRequest
) -> WhatsAppConnectionView:
    """Fully replaces which of this connection's groups/personal numbers
    feed the property/requirement pipeline (a single selection — which of
    the two a message becomes is decided by its content, not by which list
    it's in). Only valid for a connection that currently has the Property
    role."""
    try:
        return whatsapp_connection_manager.set_property_requirement_selection(
            connection_id, body.group_jids, body.personal_numbers
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="No such connection.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{connection_id}", status_code=204)
def unlink_connection(connection_id: str) -> None:
    """Logs this number out and forgets it entirely — it stops appearing in
    the connected-numbers list, and re-adding the same number later means
    scanning a fresh QR code again."""
    try:
        whatsapp_connection_manager.unlink(connection_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such connection.")
