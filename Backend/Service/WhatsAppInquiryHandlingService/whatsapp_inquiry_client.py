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
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import segno
from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv
from neonize.proto.Neonize_pb2 import JID
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as ProtoMessage
from neonize.utils import Jid2String, build_jid, extract_text

from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Model.WhatsAppInquiryHandlingModel.inquiry_status import WhatsAppInquiryStatus

SESSION_DB_PATH = os.path.join(os.path.dirname(__file__), "session", "whatsapp_inquiry_session.db")

# neonize's own QR channel gives up (prints "Login event: timeout" straight
# to stdout, bypassing every callback we hook) after nobody scans any of its
# rotating codes for a while — roughly 2-2.5 minutes has been observed in
# practice. Nothing in neonize automatically starts a fresh pairing round
# after that: the underlying connection is left sitting there with a dead,
# unscannable QR image forever unless something forces it to reconnect. This
# is comfortably past that observed window so it never races a legitimate
# in-progress scan.
_PAIRING_TIMEOUT_SECONDS = 200


def _delete_session_files() -> None:
    """Removes the session db plus its SQLite WAL/SHM sidecar files (present
    whenever the db was open at the time of the crash/logout). Best-effort:
    if a file is still locked by another process, log it and move on rather
    than blocking status reporting on a cleanup that can be redone by
    restarting."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = SESSION_DB_PATH + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            step_logger.warn(f"Could not remove stale session file {path!r}: {exc!r}")


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
        self._pairing_watchdog: Optional[threading.Timer] = None
        self._needs_session_cleanup = False
        # Set at the top of start() — see WhatsAppChatMessage's sibling in
        # whatsapp_client.py for why this exists: it lets _process_message
        # tell offline backlog (delivered as ordinary MessageEv events the
        # moment this device reconnects) apart from genuinely new messages.
        self._startup_cutoff: Optional[datetime] = None

        # Resolving a sender's real phone number (from a LID) and their
        # saved-contact name involves a lookup per message; caching by
        # sender JID avoids repeating that for the same person.
        self._sender_phone_cache: Dict[str, tuple] = {}
        self._saved_name_cache: Dict[str, str] = {}

    def send_text(self, phone: str, text: str) -> bool:
        """Sends a plain-text WhatsApp message to `phone` (any parseable
        form — only digits are used to build the JID). Never raises: a
        delivery failure is logged and reported back as False so the
        caller (inquiry_pipeline_service.py) can decide what to do rather
        than crash the batch-handling thread mid-flow — for a
        business-critical pipeline, a failed send must be visible in the
        logs, never a silent crash that also drops everything after it.

        Built here as a plain `conversation` message rather than handing a
        raw string to neonize's own `send_message`, which would route it
        through its own link-preview machinery instead: that path silently
        fails to build a preview for anything without a public DNS name
        (e.g. a `localhost`/LAN-IP URL in dev — neonize's own validator
        rejects hostnames without a dot), and — separately — a bug in its
        type-selection check (`previewType is None`, which a protobuf enum
        field can never actually be) means it *always* wraps the text in an
        ExtendedTextMessage regardless, even when nothing asked for a
        preview. Sending a plain `conversation` message directly sidesteps
        both: it's exactly the message type a person sends by typing text
        and hitting send, so it gets the same client-side URL-linkification
        every WhatsApp client already applies to plain text — nothing about
        the message format decides whether a link renders as tappable.

        NOTE: a link's tap/no-tap state also depends on WhatsApp's own
        anti-spam rule, entirely outside this message's control — links
        from a sender that isn't in the recipient's contacts stay inert
        until the recipient has sent at least one reply (Android) or saved
        the sender's number (iOS). That's a property of the chat/contact,
        not of any individual message, so no field on this proto can
        override it."""
        if self._client is None:
            step_logger.error(f"Cannot send WhatsApp message to {phone}: not connected yet.")
            return False
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            step_logger.error(f"Cannot send WhatsApp message: {phone!r} has no digits.")
            return False
        try:
            self._client.send_message(build_jid(digits), ProtoMessage(conversation=text))
            return True
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to send WhatsApp message to {phone}: {exc!r}")
            return False

    def start(self) -> None:
        """Connects to WhatsApp. Blocks for the lifetime of the connection."""
        step_logger.step("Starting WhatsApp Inquiry Handling client")
        self._startup_cutoff = datetime.now(timezone.utc)
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
        self._arm_pairing_watchdog()

    def _handle_pair_status(self, _client: NewClient, ev: PairStatusEv) -> None:
        step_logger.success(f"Inquiry Handling paired with WhatsApp account +{ev.ID.User}")
        self._disarm_pairing_watchdog()
        self._on_qr_ready(None)  # QR is single-use; stop serving the stale image
        self._on_status_changed(WhatsAppInquiryStatus.PAIRING)

    def _handle_connected(self, _client: NewClient, _ev: ConnectedEv) -> None:
        # No group/personal-chat selection step for this feature — every
        # personal message is already in scope, so it's safe to go straight
        # to LISTENING on every (re)connect, unlike whatsappDataFetching
        # which must wait for an explicit monitoring selection first.
        self._disarm_pairing_watchdog()
        step_logger.success("Inquiry Handling connected to WhatsApp — listening for every personal message")
        self._on_status_changed(WhatsAppInquiryStatus.LISTENING)

    def _handle_disconnected(self, _client: NewClient, _ev: DisconnectedEv) -> None:
        self._disarm_pairing_watchdog()
        step_logger.warn("Inquiry Handling disconnected from WhatsApp")
        self._on_status_changed(WhatsAppInquiryStatus.DISCONNECTED)

    def _arm_pairing_watchdog(self) -> None:
        self._disarm_pairing_watchdog()
        timer = threading.Timer(_PAIRING_TIMEOUT_SECONDS, self._pairing_timed_out)
        timer.daemon = True
        self._pairing_watchdog = timer
        timer.start()

    def _disarm_pairing_watchdog(self) -> None:
        if self._pairing_watchdog is not None:
            self._pairing_watchdog.cancel()
            self._pairing_watchdog = None

    def _pairing_timed_out(self) -> None:
        # Nobody scanned any of the rotating QR codes in time. neonize has
        # already given up on this pairing round on its own (silently, from
        # our side), so the connection is just sitting there with a dead QR
        # that will never successfully link. `client.start()`'s blocking
        # call (`connect()`) only returns once the underlying Go context is
        # cancelled via `stop()` — calling `disconnect()` alone just closes
        # the websocket and does NOT unblock it (see neonize's own
        # `connect_with_proxy` docstring/comments). Calling `stop()` here is
        # what lets the retry loop in whatsapp_inquiry_service.py rebuild a
        # brand-new client and put up a genuinely fresh QR — instead of the
        # page being stuck showing an unscannable code forever.
        step_logger.warn("Inquiry Handling pairing timed out — nobody scanned in time. Restarting pairing with a fresh QR.")
        self._on_qr_ready(None)
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client after pairing timeout: {exc!r}")

    def _handle_logged_out(self, _client: NewClient, _ev: LoggedOutEv) -> None:
        # A logged-out device's stored identity/keys are permanently invalid
        # on WhatsApp's side — leaving them on disk doesn't just do nothing,
        # it actively breaks every future pairing attempt: neonize reopens
        # this same file on the next connect() and reuses that dead
        # identity, so the phone rejects the new QR with "Couldn't link
        # device" indefinitely until someone notices and deletes it by hand.
        # Clearing it here means the next connect() always starts from a
        # clean device and a fresh QR actually has a chance to work.
        #
        # This callback runs synchronously from *inside* the still-blocking
        # `connect()` Go call (it's invoked directly by the Go runtime), so
        # the session db is still open at this point. On Windows that's a
        # mandatory OS-level lock, so the delete attempted here reliably
        # fails with PermissionError — unlike POSIX, where unlinking an
        # open file quietly succeeds. Flagging `_needs_session_cleanup`
        # lets the reconnect loop (whatsapp_inquiry_service._run_client)
        # retry the delete once `connect()` has actually returned and the
        # file is guaranteed to be closed.
        self._disarm_pairing_watchdog()
        step_logger.error("Logged out of WhatsApp Inquiry Handling. Clearing the stale session so re-pairing starts clean.")
        _delete_session_files()
        self._needs_session_cleanup = True
        self._on_qr_ready(None)  # the QR on screen is dead the moment the session is invalidated
        self._on_status_changed(WhatsAppInquiryStatus.LOGGED_OUT)

        # LoggedOutEv alone does NOT make the blocking `connect()` call
        # return — nothing else in neonize forces that on its own, so
        # without this the reconnect loop in whatsapp_inquiry_service.py
        # never gets control back to build a new client and put up a fresh
        # QR; the page is left stuck on this LOGGED_OUT status forever.
        # `connect()` only returns once the Go context is cancelled via
        # `stop()` — `disconnect()` alone (what this used to call) merely
        # closes the websocket and does NOT unblock it, which is exactly
        # why no fresh QR ever appeared after a real-device logout. `stop()`
        # (same mechanism `_pairing_timed_out` already relies on) makes
        # `connect()` return promptly so a new pairing round actually starts
        # within seconds instead of never.
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client after logout: {exc!r}")

    def retry_pending_session_cleanup(self) -> None:
        """Called by the reconnect loop after `connect()` has fully
        returned. If a logout happened during the connection that just
        ended, the session db was still locked when `_handle_logged_out`
        tried to delete it — retry now that the file is guaranteed to be
        closed, so the next `connect()` doesn't reopen a session file that
        still carries a revoked device identity."""
        if not self._needs_session_cleanup:
            return
        _delete_session_files()
        self._needs_session_cleanup = False

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
        received_at = self._safe_timestamp(ev.Info.Timestamp)
        if self._startup_cutoff is not None and received_at < self._startup_cutoff:
            # Offline backlog delivered as ordinary MessageEv events the
            # moment this device reconnects — see whatsapp_client.py's
            # identical check for the full explanation. Without this, every
            # inquiry sent while the backend was off would be reprocessed
            # (and re-buffered/re-classified) as if it just arrived.
            return

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
            received_at=received_at,
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
