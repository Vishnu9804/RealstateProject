from enum import StrEnum


class WhatsAppStatus(StrEnum):
    """Every state the WhatsApp connection can report through the API.
    Single source of truth for status strings — Service and Controller both
    read/write these values instead of raw strings, so a frontend can drive
    the whole pairing/selection flow off this field without guessing."""

    STARTING = "starting"
    WAITING_FOR_QR_SCAN = "waiting_for_qr_scan"
    PAIRING = "pairing"
    FETCHING_GROUPS = "fetching_groups"
    AWAITING_MONITORING_SELECTION = "awaiting_monitoring_selection"
    LISTENING = "listening"
    LISTENING_NOTHING_SELECTED = "listening_nothing_selected"
    DISCONNECTED = "disconnected"
    LOGGED_OUT = "logged_out"
    CRASHED = "crashed"
