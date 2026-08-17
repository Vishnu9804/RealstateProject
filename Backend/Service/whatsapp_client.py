"""Thin wrapper around the WhatsApp Web (multi-device) protocol client
(`neonize`) — comparable to any external-SDK client class (e.g. a
"StripeClient" or "S3Client"). It owns device pairing (QR scan), listing the
linked account's groups, and turning raw protocol events into
`WhatsAppChatMessage` objects. Which groups/personal numbers to monitor is
decided outside this class (see `submit_monitoring_selection`) — it is
driven entirely by the API/UI now, never by a terminal prompt.

This is plumbing, not decision-making: it knows nothing about HTTP, in-memory
storage, or business rules — it only reports outward through the callbacks
it's given (see whatsapp_service.py, which owns and orchestrates it). It does
not belong under Model/Agent/Controller/Middleware: Agent/ is reserved for
actual agentic (LLM-driven) code, which this is not — it's a deterministic
protocol client, no reasoning or autonomy involved.
"""

from __future__ import annotations

import io
import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

import segno
from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv
from neonize.proto.Neonize_pb2 import JID
from neonize.utils import Jid2String, build_jid, extract_text

from Middleware import step_logger
from Model.group import WhatsAppGroup
from Model.personal_chat import WhatsAppPersonalChat
from Model.whatsapp_message import WhatsAppChatMessage
from Model.whatsapp_status import WhatsAppStatus

SESSION_DB_PATH = os.path.join(os.path.dirname(__file__), "session", "whatsapp_session.db")


class WhatsAppClient:
    def __init__(
        self,
        on_groups_ready: Callable[[List[WhatsAppGroup]], None],
        on_group_selection_made: Callable[[List[WhatsAppGroup]], None],
        on_personal_selection_made: Callable[[List[WhatsAppPersonalChat]], None],
        on_message: Callable[[WhatsAppChatMessage], None],
        on_qr_ready: Callable[[Optional[bytes]], None],
        on_status_changed: Callable[[WhatsAppStatus], None],
    ):
        self._on_groups_ready = on_groups_ready
        self._on_group_selection_made = on_group_selection_made
        self._on_personal_selection_made = on_personal_selection_made
        self._on_message = on_message
        self._on_qr_ready = on_qr_ready
        self._on_status_changed = on_status_changed

        self._selected_group_jids: Set[str] = set()
        self._selected_personal_jids: Set[str] = set()
        self._chat_name_by_jid: Dict[str, str] = {}
        self._latest_groups: List[WhatsAppGroup] = []
        self._client: Optional[NewClient] = None
        self._setup_started = False

        # Resolving a sender's real phone number (from a LID) and their
        # saved-contact name involves a lookup per message; caching by
        # sender JID avoids repeating that for the same person.
        self._sender_phone_cache: Dict[str, tuple] = {}
        self._saved_name_cache: Dict[str, str] = {}

    def start(self) -> None:
        """Connects to WhatsApp. Blocks for the lifetime of the connection."""
        step_logger.step("Starting WhatsApp client")
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
            "will be generated — fetch it from GET /api/whatsapp/qr and scan "
            "it with WhatsApp on your phone: Settings > Linked Devices > Link "
            "a Device. If you already paired before, this will reconnect "
            "automatically without a QR code."
        )
        self._client.connect()

    # --- event handlers ---------------------------------------------------

    def _handle_qr(self, _client: NewClient, data_qr: bytes) -> None:
        try:
            buffer = io.BytesIO()
            segno.make_qr(data_qr).save(buffer, kind="png", scale=8)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to render QR code: {exc!r}")
            return
        step_logger.step("New WhatsApp pairing QR code ready — fetch it via GET /api/whatsapp/qr")
        self._on_status_changed(WhatsAppStatus.WAITING_FOR_QR_SCAN)
        self._on_qr_ready(buffer.getvalue())

    def _handle_pair_status(self, _client: NewClient, ev: PairStatusEv) -> None:
        step_logger.success(f"Paired with WhatsApp account +{ev.ID.User}")
        self._on_qr_ready(None)  # QR is single-use; stop serving the stale image
        self._on_status_changed(WhatsAppStatus.PAIRING)

    def _handle_connected(self, client: NewClient, _ev: ConnectedEv) -> None:
        step_logger.success("Connected to WhatsApp")

        if self._setup_started:
            # ConnectedEv fires again after any reconnect (a network blip,
            # not just the first pairing). Re-running group fetch here would
            # needlessly reset the previously submitted selection — keep it.
            step_logger.step("Reconnected — continuing with the previously selected chats.")
            return
        self._setup_started = True

        # IMPORTANT: this handler runs synchronously on the WhatsApp client's
        # own event-processing thread. client.get_joined_groups() is a
        # network round-trip — running it directly here would stall that
        # thread (including incoming-message delivery) until it returns.
        # Do the group fetch on a separate thread instead, so the connection
        # keeps flowing normally no matter how long that takes.
        threading.Thread(
            target=self._setup_after_connect,
            args=(client,),
            name="whatsapp-setup",
            daemon=True,
        ).start()

    def _setup_after_connect(self, client: NewClient) -> None:
        # self._selected_group_jids / self._selected_personal_jids stay empty
        # until submit_monitoring_selection() is called (via the API), so
        # _process_message matches nothing and nothing gets captured in the
        # meantime — no separate "wait" step is needed here.
        try:
            self._on_status_changed(WhatsAppStatus.FETCHING_GROUPS)
            groups = self._fetch_groups(client)
            self._latest_groups = groups
            self._on_groups_ready(groups)

            self._on_status_changed(WhatsAppStatus.AWAITING_MONITORING_SELECTION)
            step_logger.step(
                "Waiting for group/personal-chat selection — submit it via "
                "POST /api/whatsapp/monitoring-selection."
            )
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Setup after connecting failed: {exc!r}")

    def submit_monitoring_selection(
        self, group_jids: List[str], personal_phone_numbers: List[str]
    ) -> None:
        """Called from the API layer (Controller -> Service) whenever the UI
        submits which groups/personal numbers to monitor. Fully replaces the
        previous selection, so this is also how the UI changes what's
        monitored later without restarting the client."""
        if self._client is None:
            raise RuntimeError("WhatsApp client is not connected yet.")

        wanted_group_jids = set(group_jids)
        selected_groups = [g for g in self._latest_groups if g.jid in wanted_group_jids]
        personal_chats = self._build_personal_chats(self._client, personal_phone_numbers)

        # Assign both together, only once both are fully built, so a message
        # arriving mid-computation never matches a half-updated selection.
        self._selected_group_jids = {g.jid for g in selected_groups}
        self._selected_personal_jids = {c.jid for c in personal_chats}

        self._on_group_selection_made(selected_groups)
        self._on_personal_selection_made(personal_chats)

        monitored_names = [g.name for g in selected_groups] + [
            c.phone_number for c in personal_chats
        ]
        if monitored_names:
            step_logger.step(f"Listening for new messages in: {', '.join(monitored_names)}")
            self._on_status_changed(WhatsAppStatus.LISTENING)
        else:
            step_logger.warn("Nothing selected — no messages will be captured.")
            self._on_status_changed(WhatsAppStatus.LISTENING_NOTHING_SELECTED)

    def _handle_disconnected(self, _client: NewClient, _ev: DisconnectedEv) -> None:
        step_logger.warn("Disconnected from WhatsApp")
        self._on_status_changed(WhatsAppStatus.DISCONNECTED)

    def _handle_logged_out(self, _client: NewClient, _ev: LoggedOutEv) -> None:
        step_logger.error(
            "Logged out of WhatsApp. Delete the Service/session folder and "
            "restart the server to pair again."
        )
        self._on_status_changed(WhatsAppStatus.LOGGED_OUT)

    def _handle_message(self, _client: NewClient, ev: MessageEv) -> None:
        # Runs inside a ctypes callback invoked from the underlying Go
        # client: an uncaught exception here doesn't propagate normally, it
        # just gets silently dropped (Python prints "Exception ignored..."
        # and the message is lost). Catch broadly so a single unexpected
        # value never costs us a captured message or dumps a raw traceback.
        try:
            self._process_message(ev)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Failed to process an incoming message: {exc!r}")

    def _process_message(self, ev: MessageEv) -> None:
        source = ev.Info.MessageSource
        chat_jid = Jid2String(source.Chat)

        if source.IsGroup:
            if chat_jid not in self._selected_group_jids:
                return
            chat_type = "group"
        else:
            if chat_jid not in self._selected_personal_jids:
                return
            chat_type = "personal"

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
            received_at=self._safe_timestamp(ev.Info.Timestamp),
        )
        self._on_message(message)

    def _resolve_sender_phone(self, sender_jid: JID) -> tuple:
        """Returns (display phone string, phone-number JID) for a message
        sender. WhatsApp increasingly addresses people by an opaque "LID"
        rather than their phone number — a raw LID is useless to a
        real-estate team, so resolve it back to the real number wherever
        that mapping is available."""
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
        datetime. Never raises: a bad/unexpected value must never stop a
        captured message from being shown, so this falls back to "now"
        instead of losing the message."""
        if raw_timestamp:
            try:
                return datetime.fromtimestamp(raw_timestamp / 1000)
            except (OSError, OverflowError, ValueError):
                step_logger.warn(
                    f"Could not parse message timestamp ({raw_timestamp}); using current time instead."
                )
        return datetime.now()

    # --- group selection ---------------------------------------------------

    def _fetch_groups(self, client: NewClient) -> List[WhatsAppGroup]:
        step_logger.step("Fetching your WhatsApp groups")
        raw_groups = client.get_joined_groups()

        if not raw_groups:
            # Right after connecting, WhatsApp may not have finished syncing
            # account state yet. One short retry covers that common case.
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
        step_logger.success(f"Found {len(groups)} group(s)")
        return groups

    # --- personal chat selection --------------------------------------------

    def _build_personal_chats(
        self, client: NewClient, phone_numbers: List[str]
    ) -> List[WhatsAppPersonalChat]:
        # Personal chats aren't listed upfront the way groups are — a
        # WhatsApp account can have thousands of them — so the caller
        # supplies which numbers to watch instead of picking from a list.
        chats: List[WhatsAppPersonalChat] = []
        for raw_number in phone_numbers:
            digits = "".join(ch for ch in raw_number.strip() if ch.isdigit())
            if not digits:
                continue

            phone_jid = build_jid(digits)
            phone_jid_str = Jid2String(phone_jid)
            self._chat_name_by_jid[phone_jid_str] = digits
            chats.append(WhatsAppPersonalChat(jid=phone_jid_str, phone_number=digits))

            # WhatsApp increasingly addresses some contacts by a private "LID"
            # instead of their phone number JID. Best-effort: also watch the
            # LID form so the chat still matches if that's what messages
            # arrive addressed as. Safe to skip if this account/contact has
            # no LID mapping.
            try:
                lid_jid = client.get_lid_from_pn(phone_jid)
                if lid_jid and lid_jid.User:
                    lid_jid_str = Jid2String(lid_jid)
                    self._chat_name_by_jid[lid_jid_str] = digits
                    chats.append(WhatsAppPersonalChat(jid=lid_jid_str, phone_number=digits))
            except Exception:
                pass

        if chats:
            step_logger.success(
                f"Monitoring {len(set(c.phone_number for c in chats))} personal number(s)"
            )
        return chats
