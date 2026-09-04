"""Background loop that watches every property's linked Instagram reel for
new comments, and the account's DMs for someone sharing one of those reels
— then runs the fixed reply/DM sequence (Service/InstagramInquiryHandlingService/
instagram_message_templates.py) for each one it hasn't handled before.

Polling, not a webhook: instagrapi is the unofficial private API, so there
is no webhook subscription available the way Meta's Graph API would offer
— the only option is asking Instagram "anything new?" on an interval, the
same reasoning that already applies to WhatsAppInquiryHandlingService's
neonize client, just via HTTP instead of a persistent socket. Runs on its
own daemon thread for the process's lifetime, mirroring Service/
WhatsAppDataFetchingService/whatsapp_service.py's start_agent_in_background
pattern — entirely inert (no-op every cycle) whenever Instagram isn't
connected, so it's safe to start unconditionally at startup.

Each cycle runs in two phases, both fanned out across a small worker pool
rather than executed one item after another:

  1. FETCH — one comments call per tracked reel, plus the three inbox
     calls. These are independent reads, so they run at once instead of
     N+3 round-trips stacked end to end.
  2. HANDLE — one job per new comment / new shared reel. Two people acting
     at the same moment are served at the same moment, instead of the
     second waiting out the first's entire three-message sequence.

Two invariants make that concurrency safe, and neither is optional:

  - Every job is wrapped in _guarded, so one failing event can never
    abort the rest of the cycle (previously a single unexpected exception
    anywhere in the loop skipped every remaining property and thread, with
    only one generic line in the log to show for it).
  - All work for a GIVEN person is serialized behind that person's own
    lock (_lock_for_user). Different people overlap freely — that is the
    entire speed-up — but one person's three messages can never interleave
    with another sequence to them, and two events from them can never race
    past the same "already sent?" check and double-send.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from Config.settings import get_settings
from Middleware import step_logger
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.InstagramInquiryHandlingService import (
    instagram_connection_service,
    instagram_contact_store,
    instagram_message_templates as templates,
    instagram_messenger,
    instagram_reel_matcher,
)
from Service.WhatsAppInquiryHandlingService import form_token_service

# How often to check for new comments/DMs. This is the main lever on
# response speed — a comment posted right after a cycle starts waits up to
# this long before the next cycle even looks at it. Kept well above
# "instant": instagrapi is an unofficial client hitting Instagram's private
# endpoints, and polling every second or two is exactly the kind of pattern
# that gets an account rate-limited or hit with another verification
# checkpoint (the same risk already documented in
# instagram_connection_service.py) — a real risk, not a hypothetical one,
# for something running indefinitely. 8s is a large speed-up from the
# original 45s while staying well short of that territory.
_POLL_INTERVAL_SECONDS = 8
_COMMENTS_PER_POLL = 20
_THREADS_PER_POLL = 20

# How many Instagram operations may be in flight at once.
#
# This is the dial to turn down first if the account starts seeing
# rate-limit errors or checkpoints: 1 restores exactly the old strictly-
# sequential behavior, no other change needed. It is kept deliberately
# small — every worker makes real calls against one account, and a burst of
# concurrent private-API requests is a far more bot-like signature than the
# same requests spread out. 4 comfortably covers the realistic number of
# people acting simultaneously here without approaching that line.
#
# Note the honest trade-off: instagrapi's Client (a requests.Session plus
# per-call bookkeeping) is not documented as thread-safe, and this shares
# one across workers. In practice the request path holds up; what can race
# is the client's diagnostic state (last_response/last_json), which affects
# error *reporting*, not whether a message was sent. _guarded catches any
# fallout per event and the unsent event is simply retried next cycle, so
# the worst realistic outcome is one odd log line and an 8s delay.
_MAX_WORKERS = 4

# Tracks whether the last cycle found Instagram connected, purely so the
# "not connected, skipping" state gets ONE clear log line on the moment it
# starts (and one on the moment it ends) instead of either total silence
# (the old behavior — indistinguishable from "the poller died") or a line
# every single 8s cycle forever while disconnected.
_was_connected_last_cycle: Optional[bool] = None

# One lock per Instagram user id — see the module docstring's second
# invariant. Created on demand and never removed: an entry is a bare lock
# object keyed by a user id, so even a busy account's worth of them is
# negligible, and dropping them would reintroduce the race they exist to
# prevent.
_user_locks_guard = threading.Lock()
_user_locks: dict[str, threading.Lock] = {}


def _lock_for_user(ig_user_id: str) -> threading.Lock:
    with _user_locks_guard:
        return _user_locks.setdefault(ig_user_id, threading.Lock())


def start_background_polling() -> None:
    thread = threading.Thread(target=_poll_loop, name="instagram-polling", daemon=True)
    thread.start()
    step_logger.info(
        f"Instagram comment/DM polling started (every {_POLL_INTERVAL_SECONDS}s once connected, "
        f"up to {_MAX_WORKERS} events handled at once)."
    )


def _poll_loop() -> None:
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="ig-poll") as pool:
        while True:
            started_at = time.monotonic()
            try:
                _poll_once(pool)
            except Exception as exc:  # noqa: BLE001
                # One bad cycle (a transient Instagram error, a rate limit,
                # whatever) must never kill this thread — the next cycle is
                # the retry. type(exc).__name__ is logged alongside the message
                # specifically so a recurring failure is identifiable at a
                # glance (a rate limit and a dead session look very different
                # here) instead of every failure reading as one undifferentiated
                # "something broke".
                step_logger.error(f"Instagram polling cycle failed ({type(exc).__name__}): {exc!r}")

            # Sleep only the REMAINDER of the interval, so cycles start
            # every ~8s rather than every (however long the cycle took + 8s).
            # The old fixed sleep meant a slow cycle pushed the next one
            # further out, and the delay compounded with the number of
            # tracked properties — the effective interval silently drifted
            # far past the 8s this constant advertises.
            elapsed = time.monotonic() - started_at
            time.sleep(max(0.0, _POLL_INTERVAL_SECONDS - elapsed))


def _poll_once(pool: ThreadPoolExecutor) -> None:
    global _was_connected_last_cycle
    connected = instagram_connection_service.get_status().get("stage") == "connected"
    if connected != _was_connected_last_cycle:
        if connected:
            step_logger.success("Instagram polling: connected — watching tracked reels for comments and DM shares.")
        else:
            step_logger.warn("Instagram polling: not connected — paused until reconnected (see the Connection page).")
        _was_connected_last_cycle = connected
    if not connected:
        return
    client = instagram_connection_service.get_client()
    if client is None:
        return

    # Snapshot the tracked-property list once for this whole cycle; every
    # matcher lookup below then reads that snapshot instead of re-querying
    # the store per comment and per message.
    tracked_properties = instagram_reel_matcher.refresh_tracked_properties()

    # --- Phase 1: every independent read, issued together ------------------
    comment_fetches: list[tuple[EmbeddedProperty, str, Future]] = []
    for prop in tracked_properties:
        media_pk = instagram_reel_matcher.resolve_media_pk(prop)
        if media_pk is None:
            step_logger.warn(
                f"Instagram polling: property {prop.record_id!r} has a reel link but it couldn't be resolved "
                "yet — will retry next cycle. Check the link is a real, public reel URL."
            )
            continue
        comment_fetches.append((prop, media_pk, pool.submit(_fetch_comments, client, prop, media_pk)))

    threads_fetch = pool.submit(_fetch_threads, client)

    # --- Phase 2: one job per new event, all handled concurrently ----------
    # Submitted from this thread (never from inside a worker), so a full
    # pool can only queue these jobs, never deadlock waiting on them.
    handling: list[Future] = []
    for prop, media_pk, fetch in comment_fetches:
        for comment in fetch.result():
            handling.append(pool.submit(_guarded, _handle_comment, f"comment on {prop.record_id!r}", prop, media_pk, comment))

    seen_thread_ids: set = set()
    for thread in threads_fetch.result():
        if thread.pk in seen_thread_ids:
            continue
        seen_thread_ids.add(thread.pk)
        handling.append(pool.submit(_guarded, _handle_thread, f"DM thread {thread.pk}", thread))

    # Let the cycle finish before the next one starts. Overlapping cycles
    # would re-fetch and re-dispatch events whose handling is still in
    # flight — the per-user locks would hold the sequence together, but only
    # after each duplicate had already queued a worker for nothing.
    for job in handling:
        job.result()


def _fetch_comments(client, prop: EmbeddedProperty, media_pk: str) -> list:
    try:
        return client.media_comments(media_pk, amount=_COMMENTS_PER_POLL)
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(
            f"Instagram polling: could not fetch comments for property {prop.record_id!r} "
            f"({type(exc).__name__}): {exc}"
        )
        instagram_connection_service.verify_session_now(f"comment fetch failed: {type(exc).__name__}")
        return []


def _fetch_threads(client) -> list:
    """Three separate inboxes, all fetched every cycle — not just "pending"
    + the default (Primary) box. A share from someone the account doesn't
    already follow/isn't followed by can just as easily land in the
    General folder (Instagram's Primary/General split) as in Requests
    (Pending), and a message from an account with NO prior relationship
    at all is exactly the profile most likely to miss Primary — which is
    the most likely explanation for "worked from one account, not from a
    different one" during testing."""
    threads: list = []
    for label, fetch in (
        ("pending", lambda: client.direct_pending_inbox(amount=_THREADS_PER_POLL)),
        ("primary", lambda: client.direct_threads(amount=_THREADS_PER_POLL, box="primary")),
        ("general", lambda: client.direct_threads(amount=_THREADS_PER_POLL, box="general")),
    ):
        try:
            threads.extend(fetch())
        except Exception as exc:  # noqa: BLE001
            step_logger.warn(f"Instagram polling: could not fetch the {label} DM inbox ({type(exc).__name__}): {exc}")
            instagram_connection_service.verify_session_now(f"{label} inbox fetch failed: {type(exc).__name__}")
    return threads


def _guarded(func, description: str, *args) -> None:
    """Per-event error isolation.

    Before this, nothing wrapped the individual handlers: one unexpected
    exception propagated all the way out to _poll_loop, abandoning every
    property and thread still unprocessed in that cycle. The next cycle did
    re-fetch them (nothing is marked handled until it's actually sent), so
    a one-off blip cost ~8s — but a failure that recurred every cycle, such
    as a rate limit or a bad session, silently starved everything queued
    behind it, forever, with one undifferentiated line in the log.

    `description` names the specific event, so a recurring failure points
    straight at the comment or thread causing it instead of reading as a
    generic cycle failure.
    """
    try:
        func(*args)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Instagram polling: handling {description} failed ({type(exc).__name__}): {exc!r}")


def _handle_comment(prop: EmbeddedProperty, media_pk: str, comment) -> None:
    comment_key = f"comment:{comment.pk}"
    if not instagram_contact_store.is_event_processed(comment_key):
        step_logger.info(
            f"Instagram polling: new comment {comment.pk!r} from @{comment.user.username} on property "
            f"{prop.record_id!r} — replying..."
        )
        if instagram_messenger.reply_to_comment(media_pk, int(comment.pk), templates.COMMENT_REPLY_TEXT):
            instagram_contact_store.mark_event_processed(comment_key)
            step_logger.success(f"Replied to Instagram comment {comment.pk} on property {prop.record_id!r}.")

    commenter_id = str(comment.user.pk)
    _maybe_send_property_sequence(
        prop,
        ig_user_id=commenter_id,
        ig_username=comment.user.username,
        send=lambda text: instagram_messenger.send_dm_to_user(commenter_id, text),
        # Repeat comments from the same person on the same property share
        # one DM sequence, not one per comment — otherwise someone commenting
        # three times in a row gets the full sequence three times.
        dedupe_key=f"dm_sent:comment:{prop.record_id}:{commenter_id}",
    )


def _handle_thread(thread) -> None:
    for message in thread.messages:
        if message.is_sent_by_viewer:
            continue  # our own outbound message, not something to react to

        message_key = f"dm_message:{message.id}"
        if instagram_contact_store.is_event_processed(message_key):
            continue

        prop = _match_shared_reel(message)
        if prop is None:
            # Either not a reel share at all, or a reel share that matched
            # nothing we track — either way there's nothing to act on, and
            # no reason to look at this exact message again.
            instagram_contact_store.mark_event_processed(message_key)
            continue

        sender_id = str(message.user_id) if message.user_id else None
        if sender_id is None:
            instagram_contact_store.mark_event_processed(message_key)
            continue
        sender = next((u for u in thread.users if str(u.pk) == sender_id), None)
        step_logger.info(
            f"Instagram polling: new shared reel from @{sender.username if sender else sender_id} matches "
            f"property {prop.record_id!r} (thread {thread.pk}, pending={thread.pending}) — replying..."
        )

        if thread.pending:
            try:
                instagram_connection_service.get_client().direct_pending_approve(int(thread.pk))
                step_logger.info(f"Instagram polling: approved pending DM thread {thread.pk}.")
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(f"Could not approve pending Instagram DM thread {thread.pk} ({type(exc).__name__}): {exc}")
                instagram_connection_service.verify_session_now(f"pending thread approval failed: {type(exc).__name__}")

        thread_pk = int(thread.pk)

        # Marked processed only once actually sent (see
        # _maybe_send_property_sequence's return value) — a transient send
        # failure must be retried next cycle, not silently lost because the
        # message looked "handled" the moment it was seen.
        sent = _maybe_send_property_sequence(
            prop,
            ig_user_id=sender_id,
            ig_username=sender.username if sender else None,
            send=lambda text: instagram_messenger.send_dm_to_thread(thread_pk, text),
            # No dedupe_key here, deliberately: this function's own
            # "already sent" tracking is keyed on message_key below, unique
            # per shared message. Reusing the comment path's per-(property,
            # person) dedupe here would (and, before this fix, did)
            # silently skip a genuine new share from someone who'd already
            # been DMed about this property via a comment — sharing is its
            # own distinct, deliberate action and should always get a
            # reply.
            dedupe_key=None,
        )
        if sent:
            instagram_contact_store.mark_event_processed(message_key)


def _match_shared_reel(message) -> Optional[EmbeddedProperty]:
    """A reel shared into DM arrives in one of two shapes depending on
    which Instagram client/version sent it — confirmed against a real
    share, not assumed:
      - legacy "clip": message.clip is a full Media object with a numeric
        pk, matched via the media-pk cache (instagram_reel_matcher).
      - current "xma_clip": message.clip is never set; instead
        message.xma_share.video_url holds the reel's own permalink
        (e.g. "https://www.instagram.com/reel/DC4P0w1ilgK/..."), matched by
        extracting and comparing the short code — no extra API call needed.
    """
    if message.item_type == "clip" and message.clip is not None:
        return instagram_reel_matcher.find_property_by_media_pk(str(message.clip.pk))
    if message.item_type == "xma_clip" and message.xma_share is not None:
        code = instagram_reel_matcher.extract_reel_code(message.xma_share.video_url)
        if code:
            return instagram_reel_matcher.find_property_by_reel_code(code)
    return None


def _maybe_send_property_sequence(
    prop: EmbeddedProperty, *, ig_user_id: str, ig_username: Optional[str], send, dedupe_key: Optional[str]
) -> bool:
    """Returns True whenever there's nothing left to retry — either the
    sequence was actually sent, or there was a good reason not to (already
    converted to WhatsApp, or dedupe_key says this was already handled).
    Only False on a genuine send failure, so a caller tracking its own
    message-level idempotency (see _handle_thread) knows to leave that
    message unmarked and retry it next cycle.

    Held under this person's own lock for its whole duration — see the
    module docstring. Two events from the same person (two comments, or a
    comment and a share arriving together) would otherwise both pass the
    "already sent?" check before either had marked it, and both send; and
    two overlapping sequences to one person would interleave their three
    messages into nonsense. Different people never contend for this lock,
    so the concurrency this exists to make safe is fully preserved.
    """
    with _lock_for_user(ig_user_id):
        existing_contact = instagram_contact_store.get_contact(ig_user_id)
        if existing_contact is not None and existing_contact.linked_phone:
            # Already gave a WhatsApp number — all further contact happens
            # there, never both channels at once.
            return True

        if dedupe_key is not None and instagram_contact_store.is_event_processed(dedupe_key):
            return True

        token = form_token_service.issue_token(channel="instagram", identity=ig_user_id)
        form_link = f"{get_settings().inquiry_form_base_url.rstrip('/')}/{token}"

        sent = (
            send(templates.build_property_info_message(prop))
            and send(templates.DM_FOLLOWUP_TEXT)
            and send(templates.build_more_options_message(form_link))
        )
        if not sent:
            step_logger.error(
                f"Failed to send the full Instagram DM sequence to user {ig_user_id!r} — will retry next poll."
            )
            return False

        if dedupe_key is not None:
            instagram_contact_store.mark_event_processed(dedupe_key)
        instagram_contact_store.upsert_contact(
            existing_contact.model_copy(update={"ig_username": ig_username or existing_contact.ig_username})
            if existing_contact is not None
            else InstagramContactRecord(ig_user_id=ig_user_id, ig_username=ig_username, status="new")
        )
        step_logger.success(f"Sent Instagram DM sequence for property {prop.record_id!r} to user {ig_user_id!r}.")
        return True
