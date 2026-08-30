from enum import StrEnum


class WhatsAppInquiryStatus(StrEnum):
    """Every state the whatsappInquiryHandling WhatsApp connection can report
    through the API. This feature pairs a second, independent linked device
    (its own session, separate from WhatsAppDataFetchingService) and — unlike
    that feature — never waits on a group/personal-chat selection: every
    inbound personal message is monitored as soon as pairing finishes, so
    connection goes straight from PAIRING to LISTENING."""

    STARTING = "starting"
    WAITING_FOR_QR_SCAN = "waiting_for_qr_scan"
    PAIRING = "pairing"
    LISTENING = "listening"
    DISCONNECTED = "disconnected"
    LOGGED_OUT = "logged_out"
    CRASHED = "crashed"
