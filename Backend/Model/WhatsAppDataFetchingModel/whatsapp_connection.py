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
    Property/Requirement groups picker."""

    connection_id: str
    phone_number: Optional[str] = None
    status: WhatsAppStatus
    roles: List[ConnectionRole] = []
    joined_groups: List[WhatsAppGroup] = []
    # ONE selection — groups/personal numbers watched for BOTH property
    # listings and broker requirements. Which of the two a given message
    # actually is gets decided by its content (see
    # requirement_filter_service.matched_signal), not by picking it twice in
    # two separate lists — see whatsapp_connection_manager.py's module
    # docstring.
    property_requirement_group_jids: List[str] = []
    property_requirement_personal_numbers: List[str] = []
    is_pending: bool = False
    """True for the not-yet-paired onboarding slot the QR code currently
    belongs to — excluded from role assignment until it actually pairs."""


class UpdateRolesRequest(BaseModel):
    roles: List[ConnectionRole] = []


class PropertyRequirementSelectionRequest(BaseModel):
    """Fully replaces which of a connection's groups/personal numbers feed
    BOTH the property and the requirement pipelines — a single selection,
    not two."""

    group_jids: List[str] = []
    personal_numbers: List[str] = []
