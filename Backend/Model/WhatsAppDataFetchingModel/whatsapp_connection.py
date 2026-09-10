from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.group import WhatsAppGroup
from Model.WhatsAppDataFetchingModel.whatsapp_status import WhatsAppStatus


class ConnectionRole(StrEnum):
    """What a connected WhatsApp number is used for. Not mutually exclusive
    — the same number can feed both pipelines at once (see
    whatsapp_connection_manager.py's dispatch rule for how a message is
    routed when both roles are present)."""

    PROPERTY = "property"
    INQUIRY = "inquiry"


class WhatsAppConnectionView(BaseModel):
    """One linked WhatsApp number, as the Connection page needs to see it.
    `joined_groups` is included directly so the frontend never has to fetch
    per-connection group lists separately to build the aggregated
    Property/Requirement groups pickers — both pickers are built from this
    one list."""

    connection_id: str
    phone_number: Optional[str] = None
    status: WhatsAppStatus
    roles: List[ConnectionRole] = []
    joined_groups: List[WhatsAppGroup] = []
    property_group_jids: List[str] = []
    property_personal_numbers: List[str] = []
    # The Requirement selection — an entirely separate set from the Property
    # one above, over the same joined_groups. Overlap between the two is
    # allowed and meaningful (see whatsapp_connection_manager.py's module
    # docstring), so the UI must render these two independently and never
    # show one as "already selected" because of the other.
    requirement_group_jids: List[str] = []
    requirement_personal_numbers: List[str] = []
    is_pending: bool = False
    """True for the not-yet-paired onboarding slot the QR code currently
    belongs to — excluded from role assignment until it actually pairs."""


class UpdateRolesRequest(BaseModel):
    roles: List[ConnectionRole] = []


class PropertySelectionRequest(BaseModel):
    group_jids: List[str] = []
    personal_numbers: List[str] = []


class RequirementSelectionRequest(BaseModel):
    """Same shape as PropertySelectionRequest, kept as its own type rather
    than reused so the two endpoints can never be confused for one another
    in the generated API schema — they write to different sets."""

    group_jids: List[str] = []
    personal_numbers: List[str] = []
