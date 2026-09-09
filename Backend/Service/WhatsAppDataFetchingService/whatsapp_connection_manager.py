"""Owns every linked WhatsApp number the app knows about — the multi-number
replacement for the old one-client-per-feature setup (whatsapp_service.py's
single property client, whatsapp_inquiry_service.py's single inquiry
client). This is the piece the redesigned Connection page's "connected
numbers" / Property / Inquiry blocks are actually driven by.

Design
------
Each linked number is a `_Connection`, wrapping one `WhatsAppConnectionClient`
(one neonize session, one session db file, one QR/pairing lifecycle). A
connection can be assigned any combination of roles ("property", "inquiry")
via `set_roles`, and — only meaningful for "property" — a Property group/
personal-number selection via `set_property_selection`.

Onboarding a new number is on-demand, not always-running: `start_onboarding()`
spins up one extra, not-yet-paired connection — the "pending" slot — only
when the operator asks for one (the Connection page's "Add a number"
button), and `get_pending_qr()` serves its QR code while it's up. Pairing it
graduates it into a real, persisted connection and clears the pending slot
(see `_promote_pending_connection`) rather than immediately starting another
one — a fresh QR is requested explicitly, not kept running in the
background waiting for someone to eventually scan it. `cancel_onboarding()`
tears one down early if the operator backs out before scanning, and
`_handle_pairing_timeout` tears it down automatically if nobody scans before
its own pairing watchdog gives up.

Dispatch rule (per incoming message, see `_handle_message`): a message is
"claimed by Property" for its connection if that connection has the
"property" role AND the message's chat is in that connection's Property
group/personal selection — in which case it goes to the property pipeline,
and it is deliberately EXCLUDED from Inquiry even if the same connection
also has the "inquiry" role. Inquiry itself only ever considers PERSONAL
(1:1) chats — a group message never reaches Inquiry on any connection,
claimed by Property or not, since a group has no single owner to key a
client record on and the operator wants group traffic kept out of Inquiry
entirely, not just whatever subset Property happens to be watching. A
connection with neither role, a group message on an inquiry-only
connection, or a message matching neither rule, is dropped without
reaching any pipeline or LLM call. This is what makes "the same number for
both, but never double-counted" possible.

Migration
---------
On first run under this module, if nothing has been persisted yet but the
OLD single-session files from before this feature existed are found on
disk (Service/WhatsAppDataFetchingService/session/whatsapp_session.db for
property, Service/WhatsAppInquiryHandlingService/session/
whatsapp_inquiry_session.db for inquiry), each is adopted in place — same
file path, so the existing pairing is reused with no re-scan — as its own
connection, with roles/selections carried over from the old
monitoring_selection_store. See `_load_or_migrate_roster`.

Concurrency note
-----------------
The underlying neonize/whatsmeow Go library has an internal shared map that
isn't synchronized against concurrent client construction — starting two
clients at the exact same instant can crash the ENTIRE process with an
unrecoverable Go-runtime "fatal error: concurrent map writes" (this is the
same issue main.py used to work around by hand-staggering its two clients
by 8 seconds). `_serialize_start` generalizes that mitigation to any number
of connections: every client construction goes through one global lock that
also enforces an 8-second gap since the last one, no matter how many
connections are starting (at boot, from migration, or a brand new pending
slot after a pairing event).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.group import WhatsAppGroup
from Model.WhatsAppDataFetchingModel.whatsapp_connection import ConnectionRole, WhatsAppConnectionView
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Model.WhatsAppDataFetchingModel.whatsapp_status import WhatsAppStatus
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.WhatsAppDataFetchingService import monitoring_selection_store, whatsapp_connections_store
from Service.WhatsAppDataFetchingService.whatsapp_connection_client import WhatsAppConnectionClient

_STAGGER_SECONDS = 8
_RECONNECT_DELAY_SECONDS = 5
# A connection whose status/QR/groups has not changed at all in this long is
# treated as permanently wedged and force-rebuilt from scratch (see
# `_sweep_stuck_connections`) — comfortably past WhatsAppConnectionClient's
# own 200-second pairing-timeout-and-retry cycle, so a connection that is
# merely slow (still inside its own retry) is never swept prematurely. This
# exists because the underlying neonize/whatsmeow `stop()` call, which that
# 200-second cycle relies on to unblock a dead QR wait, has been observed to
# not always actually unblock the connection — without this, "the QR code
# must always stay available" would not hold.
_STUCK_TIMEOUT_SECONDS = 240
_STUCK_SWEEP_INTERVAL_SECONDS = 30

_SESSION_DIR = os.path.join(os.path.dirname(__file__), "session")
_LEGACY_PROPERTY_SESSION_DB = os.path.join(_SESSION_DIR, "whatsapp_session.db")
_LEGACY_INQUIRY_SESSION_DB = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "WhatsAppInquiryHandlingService", "session", "whatsapp_inquiry_session.db")
)


def _connection_session_db_path(connection_id: str) -> str:
    return os.path.join(_SESSION_DIR, f"connection_{connection_id}.db")


def _normalize_digits(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


@dataclass
class _Connection:
    connection_id: str
    session_db_path: str
    client: WhatsAppConnectionClient
    is_pending: bool
    status: WhatsAppStatus = WhatsAppStatus.STARTING
    phone_number: Optional[str] = None
    roles: Set[str] = field(default_factory=set)
    property_group_jids: Set[str] = field(default_factory=set)
    property_personal_numbers: Set[str] = field(default_factory=set)
    joined_groups: List[WhatsAppGroup] = field(default_factory=list)
    qr_png: Optional[bytes] = None
    _promoted: bool = False
    # Bumped on every real status/QR/groups/pairing callback. Used only to
    # detect a connection that has stopped making progress entirely (see
    # `_sweep_stuck_connections`) — a healthy connection cycling through
    # disconnected/reconnecting normally keeps bumping this, so it is never
    # mistaken for stuck.
    last_activity: float = field(default_factory=time.monotonic)


# --- module state ------------------------------------------------------------

_lock = threading.Lock()
_connections: Dict[str, _Connection] = {}
_pending_connection_id: Optional[str] = None

_start_lock = threading.Lock()
_last_start_time = 0.0

# Wired in by whatsapp_service / whatsapp_inquiry_service at import time
# (see their own modules) so this module never has to import either of
# them directly — both already import Service/WhatsAppDataFetchingService
# modules, and whatsapp_inquiry_service already imports things from the
# WhatsAppDataFetchingService package's sibling, so importing back from here
# risks a cycle. Same pattern as outbound_messenger.py's set_client.
_on_property_message: Optional[Callable[[WhatsAppChatMessage], None]] = None
_on_inquiry_message: Optional[Callable[[InquiryChatMessage], None]] = None


def register_property_handler(handler: Callable[[WhatsAppChatMessage], None]) -> None:
    global _on_property_message
    _on_property_message = handler


def register_inquiry_handler(handler: Callable[[InquiryChatMessage], None]) -> None:
    global _on_inquiry_message
    _on_inquiry_message = handler


def start_agent_in_background() -> None:
    """Starts the connection manager on a background thread so it never
    blocks the FastAPI/Uvicorn event loop. Restores every persisted
    connection (migrating the old single-session setup the first time).
    Onboarding a NEW number is on-demand (see `start_onboarding`) rather
    than always running in the background — a pairing QR nobody is looking
    at yet is otherwise just a WhatsApp session sitting there generating
    noise (and, eventually, timing out) for no reason."""
    thread = threading.Thread(target=_bootstrap, name="whatsapp-connections-bootstrap", daemon=True)
    thread.start()
    watchdog = threading.Thread(target=_stuck_connection_watchdog_loop, name="whatsapp-connections-watchdog", daemon=True)
    watchdog.start()


def _bootstrap() -> None:
    roster = _load_or_migrate_roster()
    for entry in roster:
        _restore_connection(entry)


def _stuck_connection_watchdog_loop() -> None:
    while True:
        time.sleep(_STUCK_SWEEP_INTERVAL_SECONDS)
        try:
            _sweep_stuck_connections()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Stuck-connection sweep failed: {exc!r}")


def _sweep_stuck_connections() -> None:
    now = time.monotonic()
    with _lock:
        stuck = [
            conn
            for conn in _connections.values()
            if conn.status != WhatsAppStatus.LISTENING and now - conn.last_activity > _STUCK_TIMEOUT_SECONDS
        ]
    for conn in stuck:
        _replace_stuck_connection(conn)


def _replace_stuck_connection(old_conn: _Connection) -> None:
    """A connection that hasn't made any progress in `_STUCK_TIMEOUT_SECONDS`
    is assumed permanently wedged (its underlying blocking call never
    returned) — rebuilt from scratch under a brand-new `_Connection` object
    with the same identity (connection_id/roles/selection preserved for a
    confirmed connection; simply dropped and replaced with a fresh pending
    slot if it was still onboarding). Using a new object, not mutating the
    old one in place, is what lets the old, possibly-still-wedged thread's
    `still_tracked` check in `_run_client_loop` correctly see it's no longer
    current and quietly give up if it ever does wake up later, instead of
    stomping on the replacement."""
    new_conn: Optional[_Connection] = None
    with _lock:
        current = _connections.get(old_conn.connection_id)
        if current is not old_conn:
            return  # already replaced/removed by something else
        step_logger.warn(
            f"Connection {old_conn.connection_id} made no progress for over {_STUCK_TIMEOUT_SECONDS:g}s "
            f"(status stuck at {old_conn.status!r}) — rebuilding it from scratch."
        )
        if old_conn.is_pending:
            del _connections[old_conn.connection_id]
            global _pending_connection_id
            if _pending_connection_id == old_conn.connection_id:
                _pending_connection_id = None
        else:
            new_client = _build_client(old_conn.connection_id, old_conn.session_db_path)
            new_conn = _Connection(
                connection_id=old_conn.connection_id,
                session_db_path=old_conn.session_db_path,
                client=new_client,
                is_pending=False,
                phone_number=old_conn.phone_number,
                roles=set(old_conn.roles),
                property_group_jids=set(old_conn.property_group_jids),
                property_personal_numbers=set(old_conn.property_personal_numbers),
                joined_groups=list(old_conn.joined_groups),
                _promoted=True,
            )
            _connections[old_conn.connection_id] = new_conn

    old_conn.client.stop_permanently()  # best-effort; orphans the old thread harmlessly if it's truly wedged
    if new_conn is not None:
        _start_client_loop(new_conn)
    # else: it was still onboarding — just dropped, matching the on-demand
    # model (no auto-replacement QR; the operator clicks "Add a number"
    # again via start_onboarding() if they still want to link one).


def _load_or_migrate_roster() -> List[dict]:
    saved = whatsapp_connections_store.load()
    if saved is not None:
        return saved

    migrated: List[dict] = []
    if os.path.exists(_LEGACY_PROPERTY_SESSION_DB):
        saved_selection = monitoring_selection_store.load()
        group_jids, personal_numbers = saved_selection if saved_selection else ([], [])
        migrated.append(
            {
                "connection_id": "legacy-property",
                "session_db_path": _LEGACY_PROPERTY_SESSION_DB,
                "roles": ["property"],
                "property_group_jids": list(group_jids),
                "property_personal_numbers": [_normalize_digits(n) for n in personal_numbers],
                "phone_number": None,
            }
        )
        step_logger.step("Migrated the existing property WhatsApp session into the new connections roster.")
    if os.path.exists(_LEGACY_INQUIRY_SESSION_DB):
        migrated.append(
            {
                "connection_id": "legacy-inquiry",
                "session_db_path": _LEGACY_INQUIRY_SESSION_DB,
                "roles": ["inquiry"],
                "property_group_jids": [],
                "property_personal_numbers": [],
                "phone_number": None,
            }
        )
        step_logger.step("Migrated the existing inquiry-handling WhatsApp session into the new connections roster.")

    if migrated:
        whatsapp_connections_store.save(migrated)
    return migrated


def _restore_connection(entry: dict) -> None:
    connection_id = entry["connection_id"]
    session_db_path = entry.get("session_db_path") or _connection_session_db_path(connection_id)
    client = _build_client(connection_id, session_db_path)
    conn = _Connection(
        connection_id=connection_id,
        session_db_path=session_db_path,
        client=client,
        is_pending=False,
        phone_number=entry.get("phone_number"),
        roles=set(entry.get("roles", [])),
        property_group_jids=set(entry.get("property_group_jids", [])),
        property_personal_numbers=set(_normalize_digits(n) for n in entry.get("property_personal_numbers", [])),
        _promoted=True,
    )
    with _lock:
        _connections[connection_id] = conn
    _start_client_loop(conn)


def _spawn_pending_connection() -> None:
    global _pending_connection_id
    connection_id = uuid.uuid4().hex[:12]
    session_db_path = _connection_session_db_path(connection_id)
    client = _build_client(connection_id, session_db_path)
    conn = _Connection(connection_id=connection_id, session_db_path=session_db_path, client=client, is_pending=True)
    with _lock:
        _connections[connection_id] = conn
        _pending_connection_id = connection_id
    _start_client_loop(conn)


def _build_client(connection_id: str, session_db_path: str) -> WhatsAppConnectionClient:
    return WhatsAppConnectionClient(
        connection_id=connection_id,
        session_db_path=session_db_path,
        on_groups_ready=_handle_groups_ready,
        on_message=_handle_message,
        on_qr_ready=_handle_qr_ready,
        on_status_changed=_handle_status_changed,
        on_paired=_handle_paired,
        on_pairing_timeout=_handle_pairing_timeout,
    )


def _start_client_loop(conn: _Connection) -> None:
    thread = threading.Thread(
        target=_run_client_loop, args=(conn,), name=f"whatsapp-client-{conn.connection_id}", daemon=True
    )
    thread.start()


def _run_client_loop(conn: _Connection) -> None:
    """Mirrors whatsapp_service.py's old `_run_client` (now superseded) —
    `client.start()` blocks for as long as the connection is alive, and a
    stream conflict/logout/rejected pairing can make it return (or raise)
    without the process dying. Looping here means a bad connection heals
    itself. Stops for good once `stop_permanently()` has been called on this
    connection's client (see `unlink`)."""
    while True:
        _serialize_start()
        client = conn.client
        try:
            client.start()
            if not client.stopped_permanently:
                step_logger.warn(f"Connection {conn.connection_id}: WhatsApp connection ended; reconnecting...")
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Connection {conn.connection_id}: client stopped unexpectedly: {exc!r}; reconnecting...")
        client.retry_pending_session_cleanup()
        if client.stopped_permanently:
            return
        _handle_status_changed(conn.connection_id, WhatsAppStatus.DISCONNECTED)
        time.sleep(_RECONNECT_DELAY_SECONDS)
        # A fresh client instance every retry, exactly like the single-client
        # loop this replaces — combined with the logout handler clearing the
        # stale session file, the next attempt gets a genuinely fresh
        # device/QR instead of reusing whatever broke last time.
        with _lock:
            still_tracked = _connections.get(conn.connection_id) is conn
        if not still_tracked:
            return
        conn.client = _build_client(conn.connection_id, conn.session_db_path)


def _serialize_start() -> None:
    """Enforces at least `_STAGGER_SECONDS` between the start of any two
    client connections, process-wide — see the module docstring's
    Concurrency note."""
    global _last_start_time
    with _start_lock:
        wait = _STAGGER_SECONDS - (time.monotonic() - _last_start_time)
        if wait > 0:
            time.sleep(wait)
        _last_start_time = time.monotonic()


# --- client callbacks ---------------------------------------------------------


def _handle_groups_ready(connection_id: str, groups: List[WhatsAppGroup]) -> None:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is not None:
            conn.joined_groups = groups
            conn.last_activity = time.monotonic()


def _handle_qr_ready(connection_id: str, png_bytes: Optional[bytes]) -> None:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is not None:
            conn.qr_png = png_bytes
            conn.last_activity = time.monotonic()


def _handle_status_changed(connection_id: str, status: WhatsAppStatus) -> None:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is not None:
            conn.status = status
            conn.last_activity = time.monotonic()


def _handle_paired(connection_id: str, phone_number: Optional[str]) -> None:
    should_promote = False
    with _lock:
        conn = _connections.get(connection_id)
        if conn is None:
            return
        conn.last_activity = time.monotonic()
        if phone_number:
            conn.phone_number = phone_number
        if conn.is_pending and not conn._promoted and phone_number:
            conn._promoted = True
            conn.is_pending = False
            should_promote = True
    if should_promote:
        _promote_pending_connection(connection_id)


def _promote_pending_connection(connection_id: str) -> None:
    """The onboarding slot just paired for the first time — it graduates
    into a real, persisted connection (roles empty until the operator
    assigns one on the Connection page). Onboarding is on-demand, so no
    replacement pending slot is started here — the operator clicks "Add a
    number" again (see `start_onboarding`) if they want to link another."""
    global _pending_connection_id
    with _lock:
        if _pending_connection_id == connection_id:
            _pending_connection_id = None
    step_logger.success(f"New WhatsApp number linked as connection {connection_id}.")
    _persist_roster()


def _handle_pairing_timeout(connection_id: str) -> None:
    """Nobody scanned this connection's QR before its pairing watchdog gave
    up. For a still-onboarding connection (the common case now that
    onboarding is on-demand) that means abandoning it outright — dropping it
    quietly and clearing the pending slot, so the Connection page just goes
    back to showing "Add a number" instead of silently generating another
    code nobody asked to see. A previously-linked number re-pairing after a
    logout is left alone here: its own reconnect loop already retries
    automatically once `client.stop()` (called right after this) unblocks
    it, exactly like any other disconnect."""
    with _lock:
        conn = _connections.get(connection_id)
        if conn is None or not conn.is_pending:
            return
        del _connections[connection_id]
        global _pending_connection_id
        if _pending_connection_id == connection_id:
            _pending_connection_id = None
    conn.client.stop_permanently()
    step_logger.warn(f"Onboarding connection {connection_id} abandoned — nobody scanned the QR in time.")


def _handle_message(connection_id: str, message: WhatsAppChatMessage) -> None:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is None or conn.is_pending:
            return
        roles = set(conn.roles)
        property_claimed = (
            ConnectionRole.PROPERTY in roles
            and (
                (message.chat_type == "group" and message.chat_jid in conn.property_group_jids)
                or (message.chat_type == "personal" and _normalize_digits(message.sender_phone) in conn.property_personal_numbers)
            )
        )
        # Inquiry NEVER looks at group messages, on any connection, whether
        # or not that group is claimed by Property — a group chat has no
        # single owner to key a client record on, and the operator has been
        # explicit that group traffic must stay fully out of Inquiry, not
        # just the specific groups Property happens to be watching. Only a
        # personal (1:1) chat can be an inquiry, and even then only if
        # Property on this same connection hasn't already claimed that
        # exact number.
        inquiry_eligible = ConnectionRole.INQUIRY in roles and message.chat_type == "personal" and not property_claimed

    if property_claimed and _on_property_message is not None:
        _on_property_message(message)
    if inquiry_eligible and _on_inquiry_message is not None:
        _on_inquiry_message(_to_inquiry_message(message))


def _to_inquiry_message(message: WhatsAppChatMessage) -> InquiryChatMessage:
    return InquiryChatMessage(
        message_id=message.message_id,
        sender_jid=message.sender_jid,
        sender_phone=message.sender_phone,
        sender_name=message.sender_name,
        sender_saved_name=message.sender_saved_name,
        text=message.text,
        received_at=message.received_at,
    )


def _persist_roster() -> None:
    with _lock:
        roster = [
            {
                "connection_id": conn.connection_id,
                "session_db_path": conn.session_db_path,
                "roles": sorted(conn.roles),
                "property_group_jids": sorted(conn.property_group_jids),
                "property_personal_numbers": sorted(conn.property_personal_numbers),
                "phone_number": conn.phone_number,
            }
            for conn in _connections.values()
            if not conn.is_pending
        ]
    whatsapp_connections_store.save(roster)


# --- public API (Controller-facing) -------------------------------------------


def list_connections() -> List[WhatsAppConnectionView]:
    with _lock:
        views = [
            WhatsAppConnectionView(
                connection_id=conn.connection_id,
                phone_number=conn.phone_number,
                status=conn.status,
                roles=[ConnectionRole(r) for r in sorted(conn.roles)],
                joined_groups=list(conn.joined_groups),
                property_group_jids=sorted(conn.property_group_jids),
                property_personal_numbers=sorted(conn.property_personal_numbers),
                is_pending=conn.is_pending,
            )
            for conn in _connections.values()
        ]
    # Pending slot last — the confirmed, usable numbers are what the operator
    # actually needs to scan through first.
    views.sort(key=lambda v: (v.is_pending, v.phone_number or v.connection_id))
    return views


def get_pending_qr() -> Optional[bytes]:
    with _lock:
        if _pending_connection_id is None:
            return None
        conn = _connections.get(_pending_connection_id)
        return conn.qr_png if conn else None


def start_onboarding() -> WhatsAppConnectionView:
    """Starts (or, if one is already in progress, just returns) the pending
    onboarding connection — called when the operator taps "Add a number" on
    the Connection page. Idempotent: a second click while a QR is already up
    reuses that same one instead of abandoning it and starting a duplicate."""
    with _lock:
        existing_id = _pending_connection_id
    if existing_id is not None:
        with _lock:
            conn = _connections.get(existing_id)
        if conn is not None:
            return _view_of(existing_id)
    _spawn_pending_connection()
    with _lock:
        new_id = _pending_connection_id
    assert new_id is not None
    return _view_of(new_id)


def cancel_onboarding() -> None:
    """Backs out of an in-progress onboarding before it's scanned (the
    Connection page's QR panel "Cancel" action) — no-op if nothing is
    pending."""
    global _pending_connection_id
    with _lock:
        connection_id = _pending_connection_id
        conn = _connections.get(connection_id) if connection_id else None
        if conn is None:
            return
        del _connections[connection_id]
        _pending_connection_id = None
    conn.client.stop_permanently()


def set_roles(connection_id: str, roles: List[str]) -> WhatsAppConnectionView:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is None or conn.is_pending:
            raise KeyError(connection_id)
        conn.roles = set(roles)
        if ConnectionRole.PROPERTY not in conn.roles:
            conn.property_group_jids = set()
            conn.property_personal_numbers = set()
    _persist_roster()
    return _view_of(connection_id)


def set_property_selection(connection_id: str, group_jids: List[str], personal_numbers: List[str]) -> WhatsAppConnectionView:
    with _lock:
        conn = _connections.get(connection_id)
        if conn is None or conn.is_pending:
            raise KeyError(connection_id)
        if ConnectionRole.PROPERTY not in conn.roles:
            raise ValueError("This connection does not have the property role.")
        joined_jids = {g.jid for g in conn.joined_groups}
        conn.property_group_jids = {jid for jid in group_jids if jid in joined_jids}
        conn.property_personal_numbers = {_normalize_digits(n) for n in personal_numbers if _normalize_digits(n)}
    _persist_roster()
    return _view_of(connection_id)


def unlink(connection_id: str) -> None:
    with _lock:
        conn = _connections.pop(connection_id, None)
    if conn is None:
        raise KeyError(connection_id)
    conn.client.stop_permanently()
    _persist_roster()


def _view_of(connection_id: str) -> WhatsAppConnectionView:
    with _lock:
        conn = _connections[connection_id]
        return WhatsAppConnectionView(
            connection_id=conn.connection_id,
            phone_number=conn.phone_number,
            status=conn.status,
            roles=[ConnectionRole(r) for r in sorted(conn.roles)],
            joined_groups=list(conn.joined_groups),
            property_group_jids=sorted(conn.property_group_jids),
            property_personal_numbers=sorted(conn.property_personal_numbers),
            is_pending=conn.is_pending,
        )


def get_status_summary() -> dict:
    """Aggregates every connection into the same shape the old single-client
    status used to have, so the header pill / command palette / dashboard —
    none of which know about multiple connections — keep working unchanged.
    """
    with _lock:
        confirmed = [c for c in _connections.values() if not c.is_pending]
        property_conns = [c for c in confirmed if ConnectionRole.PROPERTY in c.roles]
        joined_group_count = sum(len(c.joined_groups) for c in property_conns)
        monitored_group_count = sum(len(c.property_group_jids) for c in property_conns)
        monitored_personal_chat_count = sum(len(c.property_personal_numbers) for c in property_conns)
        statuses = [c.status for c in confirmed]

    overall = _summarize_status(statuses)
    return {
        "status": overall,
        "joined_group_count": joined_group_count,
        "monitored_group_count": monitored_group_count,
        "monitored_personal_chat_count": monitored_personal_chat_count,
    }


def get_inquiry_status_summary() -> str:
    with _lock:
        statuses = [c.status for c in _connections.values() if not c.is_pending and ConnectionRole.INQUIRY in c.roles]
    return _summarize_status(statuses)


def _summarize_status(statuses: List[WhatsAppStatus]) -> str:
    """Picks one representative status out of many connections' statuses,
    for surfaces that only ever showed a single WhatsApp connection's state
    before. Priority: anything actively listening counts as healthy; a
    connection needing attention (logged out/crashed) outranks a merely
    reconnecting one; no connections at all reads as "awaiting_monitoring_selection"
    (nothing to monitor yet), matching the old first-run empty state."""
    if not statuses:
        return WhatsAppStatus.AWAITING_MONITORING_SELECTION
    if any(s == WhatsAppStatus.LISTENING for s in statuses):
        return WhatsAppStatus.LISTENING
    if any(s in (WhatsAppStatus.LOGGED_OUT, WhatsAppStatus.CRASHED) for s in statuses):
        return next(s for s in statuses if s in (WhatsAppStatus.LOGGED_OUT, WhatsAppStatus.CRASHED))
    if any(s == WhatsAppStatus.DISCONNECTED for s in statuses):
        return WhatsAppStatus.DISCONNECTED
    return statuses[0]


def get_sender_client(prefer_role: str) -> Optional[WhatsAppConnectionClient]:
    """Picks a currently-listening client to send an outbound message
    through — preferring one with `prefer_role`, falling back to any
    listening connection at all so hand-off/welcome messages still go out
    even if nothing happens to be flagged for that role."""
    with _lock:
        candidates = [c for c in _connections.values() if not c.is_pending and c.status == WhatsAppStatus.LISTENING]
        preferred = [c for c in candidates if prefer_role in c.roles]
        chosen = preferred[0] if preferred else (candidates[0] if candidates else None)
        return chosen.client if chosen else None
