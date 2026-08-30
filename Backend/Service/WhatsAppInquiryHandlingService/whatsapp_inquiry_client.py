"""Thin wrapper around the WhatsApp Web (multi-device) protocol client
(`neonize`) for the whatsappInquiryHandling feature — same role as
Service/WhatsAppDataFetchingService/whatsapp_client.py, but this feature
pairs its own separate linked device (own session DB, own QR code) and
never gates on a group/personal-chat selection: whatsappDataFetching only
processes messages an operator has explicitly chosen to monitor, but
inquiry handling exists to catch every inbound client inquiry, so as soon
as pairing finishes it listens to every personal chat unconditionally.

Group messages are intentionally dropped — see InquiryChatMessage's
docstring. This is plumbing, not decision-making: it only reports outward
through the callbacks it's given (see whatsapp_inquiry_service.py, which
owns and orchestrates it).
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import segno
from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv
from neonize.proto.Neonize_pb2 import JID
from neonize.utils import Jid2String, extract_text

from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Model.WhatsAppInquiryHandlingModel.inquiry_status import WhatsAppInquiryStatus

SESSION_DB_PATH = os.path.join(os.path.dirname(__file__), "session", "whatsapp_inquiry_session.db")


class WhatsAppInquiryClient:
    def __init__(
        self,
        on_message: Callable[[InquiryChatMessage], None],
        on_qr_ready: Callable[[Optional[bytes]], None],
        on_status_changed: Callable[[WhatsAppInquiryStatus], None],
    ):
        self._on_message = on_message
        self._on_qr_ready = on_qr_ready
        self._on_status_changed = on_status_changed

        self._client: Optional[NewClient] = None

        # Resolving a sender's real phone number (from a LID) and their
        # saved-contact name involves a lookup per message; caching by
        # sender JID avoids repeating that for the same person.
        self._sender_phone_cache: Dict[str, tuple] = {}
        self._saved_name_cache: Dict[str, str] = {}

    def start(self) -> None:
        """Connects to WhatsApp. Blocks for the lifetime of the connection."""
        step_logger.step("Starting WhatsApp Inquiry Handling client")
        os.makedirs(os.path.dirname(SESSION_DB_PATH), exist_ok=True)

        self._client = NewClient(SESSION_DB_PATH)
        self._client.event(ConnectedEv)(self._handle_connected)
        self._client.event(PairStatusEv)(self._handle_pair_status)
        self._client.event(DisconnectedEv)(self._handle_disconnected)
        self._client.event(LoggedOutEv)(self._handle_logged_out)
        self._client.event(MessageEv)(self._handle_message)
        self._client.qr(self._handle_qr)

        step_logger.step(
            "Waiting for device pairing. If this is the first run, a QR code "
            "will be generated — fetch it from GET /api/whatsapp-inquiry/qr and "
            "scan it with WhatsApp on your phone: Settings > Linked Devices > "
            "Link a Device. This is a separate linked device from the "
            "whatsappDataFetching feature, so pairing it does not affect that "
            "connection. If you already paired before, this will reconnect "
            "automatically without a QR code."
        )
        self._client.connect()

    # --- event handlers ---------------------------------------------------

    def _handle_qr(self, _client: NewClient, data_qr: bytes) -> None:
        try:
            buffer = io.BytesIO()
            segno.make_qr(data_qr).save(buffer, kind="png", scale=8)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to render Inquiry Handling QR code: {exc!r}")
            return
        step_logger.step("New WhatsApp Inquiry Handling pairing QR code ready — fetch it via GET /api/whatsapp-inquiry/qr")
        self._on_status_changed(WhatsAppInquiryStatus.WAITING_FOR_QR_SCAN)
        self._on_qr_ready(buffer.getvalue())

    def _handle_pair_status(self, _client: NewClient, ev: PairStatusEv) -> None:
        step_logger.success(f"Inquiry Handling paired with WhatsApp account +{ev.ID.User}")
        self._on_qr_ready(None)  # QR is single-use; stop serving the stale image
        self._on_status_changed(WhatsAppInquiryStatus.PAIRING)

    def _handle_connected(self, _client: NewClient, _ev: ConnectedEv) -> None:
        # No group/personal-chat selection step for this feature — every
        # personal message is already in scope, so it's safe to go straight
        # to LISTENING on every (re)connect, unlike whatsappDataFetching
        # which must wait for an explicit monitoring selection first.
        step_logger.success("Inquiry Handling connected to WhatsApp — listening for every personal message")
        self._on_status_changed(WhatsAppInquiryStatus.LISTENING)

    def _handle_disconnected(self, _client: NewClient, _ev: DisconnectedEv) -> None:
        step_logger.warn("Inquiry Handling disconnected from WhatsApp")
        self._on_status_changed(WhatsAppInquiryStatus.DISCONNECTED)

    def _handle_logged_out(self, _client: NewClient, _ev: LoggedOutEv) -> None:
        step_logger.error(
            "Logged out of WhatsApp Inquiry Handling. Delete the "
            "Service/WhatsAppInquiryHandlingService/session folder and restart "
            "the server to pair again."
        )
        self._on_status_changed(WhatsAppInquiryStatus.LOGGED_OUT)

    def _handle_message(self, _client: NewClient, ev: MessageEv) -> None:
        # Runs inside a ctypes callback invoked from the underlying Go
        # client: an uncaught exception here doesn't propagate normally, it
        # just gets silently dropped. Catch broadly so a single unexpected
        # value never costs us a captured message or dumps a raw traceback.
        try:
            self._process_message(ev)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to process an incoming inquiry message: {exc!r}")

    def _process_message(self, ev: MessageEv) -> None:
        source = ev.Info.MessageSource

        if source.IsGroup:
            return  # inquiry handling only ever tracks personal (1:1) chats

        text = extract_text(ev.Message)
        if not text:
            return  # media with no caption, reaction, poll update, etc.

        sender_name = ev.Info.Pushname or source.Sender.User
        sender_phone, sender_phone_jid = self._resolve_sender_phone(source.Sender)
        sender_saved_name = self._resolve_saved_name(sender_phone_jid)

        message = InquiryChatMessage(
            message_id=ev.Info.ID,
            sender_jid=Jid2String(source.Sender),
            sender_phone=sender_phone,
            sender_name=sender_name,
            sender_saved_name=sender_saved_name,
            text=text,
            received_at=self._safe_timestamp(ev.Info.Timestamp),
        )
        self._on_message(message)

    def _resolve_sender_phone(self, sender_jid: JID) -> tuple:
        """Returns (display phone string, phone-number JID) for a message
        sender. WhatsApp increasingly addresses people by an opaque "LID"
        rather than their phone number — a raw LID can't be used to key a
        client record, so resolve it back to the real number wherever that
        mapping is available."""
        cache_key = Jid2String(sender_jid)
        if cache_key in self._sender_phone_cache:
            return self._sender_phone_cache[cache_key]

        phone_jid = sender_jid
        if sender_jid.Server == "lid" and self._client is not None:
            try:
                resolved = self._client.get_pn_from_lid(sender_jid)
                if resolved and resolved.User:
                    phone_jid = resolved
            except Exception:
                pass  # best-effort; falls back to showing "Unknown" below

        display = f"+{phone_jid.User}" if phone_jid.User and phone_jid.Server != "lid" else "Unknown"
        result = (display, phone_jid)
        self._sender_phone_cache[cache_key] = result
        return result

    def _resolve_saved_name(self, phone_jid: JID) -> str:
        """Looks up the name this contact is saved under in the linked
        phone's own WhatsApp contacts — distinct from their self-set
        WhatsApp push name. Returns "Unsaved" if there's no match."""
        cache_key = Jid2String(phone_jid)
        if cache_key in self._saved_name_cache:
            return self._saved_name_cache[cache_key]

        saved_name = "Unsaved"
        if self._client is not None:
            try:
                contact = self._client.contact.get_contact(phone_jid)
                if contact.Found and (contact.FullName or contact.FirstName):
                    saved_name = contact.FullName or contact.FirstName
            except Exception:
                pass  # best-effort; falls back to "Unsaved"

        self._saved_name_cache[cache_key] = saved_name
        return saved_name

    @staticmethod
    def _safe_timestamp(raw_timestamp: int) -> datetime:
        """Converts Info.Timestamp (documented as Unix milliseconds) to a
        UTC-aware datetime. Always UTC, never naive/local-clock. Never
        raises: a bad/unexpected value must never stop a captured message
        from being shown, so this falls back to "now" instead of losing the
        message."""
        if raw_timestamp:
            try:
                return datetime.fromtimestamp(raw_timestamp / 1000, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                step_logger.warn(
                    f"Could not parse inquiry message timestamp ({raw_timestamp}); using current time instead."
                )
        return datetime.now(timezone.utc)
