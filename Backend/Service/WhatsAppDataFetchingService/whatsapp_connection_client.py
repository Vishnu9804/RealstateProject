"""Thin wrapper around the WhatsApp Web (multi-device) protocol client
(`neonize`) for ONE linked number, used for every connection the app
manages — property-only, inquiry-only, or both at once (see
whatsapp_connection_manager.py, which owns and orchestrates one instance of
this per connection and decides what each captured message is used for).

Unlike the older, feature-specific clients this replaces (whatsapp_client.py
and whatsapp_inquiry_client.py — both now dead code, superseded by this
module and the connection manager), this client makes no monitoring
decisions of its own: it forwards EVERY group and personal message it
receives, unconditionally, and lets the connection manager decide per
message whether that's a property message, an inquiry message, both, or
neither, based on the connection's roles and its Property group/personal
selection. That decoupling is what makes "the same number feeds both
pipelines, with an exclusion list" possible without this class needing to
know anything about pipelines at all.

This is plumbing, not decision-making: it knows nothing about HTTP,
in-memory storage, or business rules — it only reports outward through the
callbacks it's given. It does not belong under Model/Agent/Controller/
Middleware: Agent/ is reserved for actual agentic (LLM-driven) code, which
this is not — it's a deterministic protocol client, no reasoning involved.
"""

from __future__ import annotations

import io
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import segno
from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv
from neonize.proto.Neonize_pb2 import JID
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as ProtoMessage
from neonize.utils import Jid2String, build_jid, extract_text

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.group import WhatsAppGroup
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Model.WhatsAppDataFetchingModel.whatsapp_status import WhatsAppStatus

# Same observed neonize/whatsmeow behavior as the two clients this replaces:
# roughly 2-2.5 minutes after nobody scans any of the rotating QR codes,
# neonize silently gives up on its own, leaving a dead, unscannable code on
# screen forever unless something forces a reconnect. Comfortably past that
# window so it never races a legitimate in-progress scan.
_PAIRING_TIMEOUT_SECONDS = 200


def _delete_session_files(session_db_path: str) -> None:
    """Removes the session db plus its SQLite WAL/SHM sidecar files.
    Best-effort: if a file is still locked by another process, log it and
    move on rather than blocking on a cleanup that can be redone by
    restarting."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = session_db_path + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            step_logger.warn(f"Could not remove stale session file {path!r}: {exc!r}")


class WhatsAppConnectionClient:
    def __init__(
        self,
        connection_id: str,
        session_db_path: str,
        on_groups_ready: Callable[[str, List[WhatsAppGroup]], None],
        on_message: Callable[[str, WhatsAppChatMessage], None],
        on_qr_ready: Callable[[str, Optional[bytes]], None],
        on_status_changed: Callable[[str, WhatsAppStatus], None],
        on_paired: Callable[[str, Optional[str]], None],
        on_pairing_timeout: Callable[[str], None],
    ):
        self.connection_id = connection_id
        self.session_db_path = session_db_path
        self._on_groups_ready = on_groups_ready
        self._on_message = on_message
        self._on_qr_ready = on_qr_ready
        self._on_status_changed = on_status_changed
        self._on_paired = on_paired
        self._on_pairing_timeout = on_pairing_timeout

        self._chat_name_by_jid: Dict[str, str] = {}
        self._latest_groups: List[WhatsAppGroup] = []
        self._client: Optional[NewClient] = None
        self._setup_started = False
        self._pairing_watchdog: Optional[threading.Timer] = None
        self._needs_session_cleanup = False
        self._stopped_permanently = False
        # See WhatsAppClient's identical field (the module this replaces)
        # for why this exists: it lets _process_message tell offline
        # backlog apart from genuinely new messages.
        self._startup_cutoff: Optional[datetime] = None

        self._sender_phone_cache: Dict[str, tuple] = {}
        self._saved_name_cache: Dict[str, str] = {}

    def get_latest_groups(self) -> List[WhatsAppGroup]:
        return list(self._latest_groups)

    def send_text(self, phone: str, text: str) -> bool:
        """Sends a plain-text WhatsApp message to `phone` over this
        connection. Never raises: a delivery failure is logged and reported
        back as False. Built as a plain `conversation` message rather than
        neonize's own `send_message` — see WhatsAppInquiryClient's identical
        method (the module this replaces) for why that path is unsafe for a
        local/LAN link."""
        if self._client is None:
            step_logger.error(f"Cannot send WhatsApp message via {self.connection_id}: not connected yet.")
            return False
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            step_logger.error(f"Cannot send WhatsApp message via {self.connection_id}: {phone!r} has no digits.")
            return False
        try:
            self._client.send_message(build_jid(digits), ProtoMessage(conversation=text))
            return True
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to send WhatsApp message to {phone} via {self.connection_id}: {exc!r}")
            if "not connected" in str(exc).lower():
                self._force_reconnect_after_dead_websocket()
            return False

    def _force_reconnect_after_dead_websocket(self) -> None:
        """See WhatsAppInquiryClient's identical method (the module this
        replaces) for the full explanation: a send can fail with "websocket
        not connected" without neonize ever firing DisconnectedEv, which
        would otherwise leave this connection stuck forever."""
        step_logger.warn(f"Connection {self.connection_id} websocket appears dead — forcing a reconnect.")
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client after a dead-websocket send failure ({self.connection_id}): {exc!r}")

    def start(self) -> None:
        """Connects to WhatsApp. Blocks for the lifetime of the connection."""
        step_logger.step(f"Starting WhatsApp client for connection {self.connection_id}")
        self._startup_cutoff = datetime.now(timezone.utc)
        os.makedirs(os.path.dirname(self.session_db_path), exist_ok=True)

        self._client = NewClient(self.session_db_path)
        self._client.event(ConnectedEv)(self._handle_connected)
        self._client.event(PairStatusEv)(self._handle_pair_status)
        self._client.event(DisconnectedEv)(self._handle_disconnected)
        self._client.event(LoggedOutEv)(self._handle_logged_out)
        self._client.event(MessageEv)(self._handle_message)
        self._client.qr(self._handle_qr)

        self._client.connect()

    def stop_permanently(self) -> None:
        """Marks this connection as intentionally unlinked — the reconnect
        loop that owns this client checks this flag and stops retrying once
        `start()` returns, instead of reconnecting forever. Also forces the
        blocking `connect()` call to return promptly, the same mechanism
        used for a pairing timeout or a logout."""
        self._stopped_permanently = True
        self._disarm_pairing_watchdog()
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client {self.connection_id}: {exc!r}")

    @property
    def stopped_permanently(self) -> bool:
        return self._stopped_permanently

    # --- event handlers ---------------------------------------------------

    def _handle_qr(self, _client: NewClient, data_qr: bytes) -> None:
        try:
            buffer = io.BytesIO()
            segno.make_qr(data_qr).save(buffer, kind="png", scale=8)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to render QR code for {self.connection_id}: {exc!r}")
            return
        self._on_status_changed(self.connection_id, WhatsAppStatus.WAITING_FOR_QR_SCAN)
        self._on_qr_ready(self.connection_id, buffer.getvalue())
        self._arm_pairing_watchdog()

    def _handle_pair_status(self, _client: NewClient, ev: PairStatusEv) -> None:
        self._disarm_pairing_watchdog()
        step_logger.success(f"Connection {self.connection_id} paired with WhatsApp account +{ev.ID.User}")
        self._on_qr_ready(self.connection_id, None)  # QR is single-use; stop serving the stale image
        self._on_status_changed(self.connection_id, WhatsAppStatus.PAIRING)
        self._on_paired(self.connection_id, ev.ID.User or None)

    def _handle_connected(self, client: NewClient, _ev: ConnectedEv) -> None:
        self._disarm_pairing_watchdog()
        step_logger.success(f"Connection {self.connection_id} connected to WhatsApp")

        if self._setup_started:
            step_logger.step(f"Connection {self.connection_id} reconnected.")
            return
        self._setup_started = True

        # IMPORTANT: runs synchronously on the WhatsApp client's own
        # event-processing thread — client.get_joined_groups()/get_me() are
        # network round-trips, so do them on a separate thread instead of
        # stalling incoming-message delivery until they return.
        threading.Thread(
            target=self._setup_after_connect,
            args=(client,),
            name=f"whatsapp-setup-{self.connection_id}",
            daemon=True,
        ).start()

    def _setup_after_connect(self, client: NewClient) -> None:
        try:
            self._resolve_own_phone_number(client)

            self._on_status_changed(self.connection_id, WhatsAppStatus.FETCHING_GROUPS)
            groups = self._fetch_groups(client)
            self._latest_groups = groups
            self._on_groups_ready(self.connection_id, groups)

            # No monitoring-selection gate here (unlike the property-only
            # client this replaces) — every message is forwarded, and the
            # connection manager decides what to do with it based on this
            # connection's roles/selection. So it's safe to go straight to
            # LISTENING the moment groups are known.
            step_logger.step(f"Connection {self.connection_id} listening for messages.")
            self._on_status_changed(self.connection_id, WhatsAppStatus.LISTENING)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Setup after connecting failed for {self.connection_id}: {exc!r}")

    def _resolve_own_phone_number(self, client: NewClient) -> None:
        """Best-effort — used to (re)learn this connection's own phone
        number on every reconnect, not just the initial pairing (PairStatusEv
        only fires during an actual pairing handshake, never on an ordinary
        reconnect to an already-paired session)."""
        try:
            me = client.get_me()
            phone = me.JID.User if me and me.JID else None
            if phone:
                self._on_paired(self.connection_id, phone)
        except Exception as exc:  # noqa: BLE001
            step_logger.warn(f"Could not resolve own phone number for {self.connection_id}: {exc!r}")

    def _handle_disconnected(self, _client: NewClient, _ev: DisconnectedEv) -> None:
        self._disarm_pairing_watchdog()
        step_logger.warn(f"Connection {self.connection_id} disconnected from WhatsApp")
        self._on_status_changed(self.connection_id, WhatsAppStatus.DISCONNECTED)

    def _handle_logged_out(self, _client: NewClient, _ev: LoggedOutEv) -> None:
        # See WhatsAppClient's identical handler (the module this replaces)
        # for the full explanation of why the session db must be cleared and
        # why stop() (not disconnect()) is required to unblock connect().
        self._disarm_pairing_watchdog()
        step_logger.error(f"Connection {self.connection_id} logged out of WhatsApp. Clearing the stale session.")
        _delete_session_files(self.session_db_path)
        self._needs_session_cleanup = True
        self._on_qr_ready(self.connection_id, None)
        self._on_status_changed(self.connection_id, WhatsAppStatus.LOGGED_OUT)
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client after logout ({self.connection_id}): {exc!r}")

    def retry_pending_session_cleanup(self) -> None:
        if not self._needs_session_cleanup:
            return
        _delete_session_files(self.session_db_path)
        self._needs_session_cleanup = False

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
        step_logger.warn(f"Pairing timed out for {self.connection_id} — nobody scanned in time.")
        self._on_qr_ready(self.connection_id, None)
        # Lets the manager decide what "gave up" means for this connection —
        # abandon it outright if it was still onboarding (on-demand QR, no
        # more silent auto-regenerate-forever loop), or just let the normal
        # reconnect loop retry if this was a previously-linked number
        # re-pairing after a logout. Called before stop() so the manager can
        # mark this client permanently stopped first if it chooses to.
        self._on_pairing_timeout(self.connection_id)
        if self._client is not None:
            try:
                self._client.stop()
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Error stopping client after pairing timeout ({self.connection_id}): {exc!r}")

    def _handle_message(self, _client: NewClient, ev: MessageEv) -> None:
        # Runs inside a ctypes callback invoked from the underlying Go
        # client: an uncaught exception here is silently dropped rather than
        # propagating normally. Catch broadly so one bad value never costs a
        # captured message or dumps a raw traceback.
        try:
            self._process_message(ev)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to process an incoming message on {self.connection_id}: {exc!r}")

    def _process_message(self, ev: MessageEv) -> None:
        received_at = self._safe_timestamp(ev.Info.Timestamp)
        if self._startup_cutoff is not None and received_at < self._startup_cutoff:
            # Offline backlog delivered as ordinary MessageEv events the
            # moment this device reconnects — see WhatsAppClient's identical
            # check (the module this replaces) for the full explanation.
            return

        source = ev.Info.MessageSource
        chat_jid = Jid2String(source.Chat)
        chat_type = "group" if source.IsGroup else "personal"

        text = extract_text(ev.Message)
        if not text:
            return  # media with no caption, reaction, poll update, etc.

        sender_name = ev.Info.Pushname or source.Sender.User
        sender_phone, sender_phone_jid = self._resolve_sender_phone(source.Sender)
        sender_saved_name = self._resolve_saved_name(sender_phone_jid)

        message = WhatsAppChatMessage(
            message_id=ev.Info.ID,
            chat_jid=chat_jid,
            chat_name=self._chat_name_by_jid.get(chat_jid, sender_name if chat_type == "personal" else chat_jid),
            chat_type=chat_type,
            sender_jid=Jid2String(source.Sender),
            sender_phone=sender_phone,
            sender_name=sender_name,
            sender_saved_name=sender_saved_name,
            text=text,
            received_at=received_at,
        )
        self._on_message(self.connection_id, message)

    def _resolve_sender_phone(self, sender_jid: JID) -> tuple:
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
        if raw_timestamp:
            try:
                return datetime.fromtimestamp(raw_timestamp / 1000, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                step_logger.warn(f"Could not parse message timestamp ({raw_timestamp}); using current time instead.")
        return datetime.now(timezone.utc)

    def _fetch_groups(self, client: NewClient) -> List[WhatsAppGroup]:
        step_logger.step(f"Fetching groups for connection {self.connection_id}")
        raw_groups = client.get_joined_groups()

        if not raw_groups:
            step_logger.info("No groups returned yet, waiting for account sync to finish...")
            time.sleep(3)
            raw_groups = client.get_joined_groups()

        groups = [
            WhatsAppGroup(
                jid=Jid2String(g.JID),
                name=g.GroupName.Name or "(unnamed group)",
                member_count=len(g.Participants),
            )
            for g in raw_groups
        ]
        self._chat_name_by_jid.update({g.jid: g.name for g in groups})
        step_logger.success(f"Connection {self.connection_id}: found {len(groups)} group(s)")
        return groups
