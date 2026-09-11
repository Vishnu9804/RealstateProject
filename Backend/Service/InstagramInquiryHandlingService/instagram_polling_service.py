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
     N+3 round-trips stacked end to end. "Tracked" is a bounded, in-memory
     set of the most recently linked reels, refreshed only when a property
     actually changes — see instagram_reel_matcher, which explains why a
     cycle that finds nothing new must not read the database at all.
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
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from Config.settings import get_settings
from Config.settings import get_settings
from Middleware import daily_quota, step_logger
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
# invariant. Created on demand, and now bounded: an entry is a bare lock
# object keyed by a user id, so a busy account's worth of them is
# negligible, but "one per Instagram account that ever commented" on a
# process that runs for months is not a bound at all.
#
# Eviction is safe ONLY because of the rule enforced in _lock_for_user
# below: a lock that is currently HELD is never removed. Dropping a held
# lock would hand the next caller a brand new one and let two sequences run
# for the same person at the same time — precisely the race this table
# exists to prevent — so that check is the load-bearing line, not the
# ceiling. An idle lock, by definition, is protecting nothing at that
# instant, and re-creating it later is free.
_MAX_USER_LOCKS = 10_000
_user_locks_guard = threading.Lock()
_user_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()

# Shortest gap between two DMs to the same person about the same property.
#
# Someone commenting four times in twenty seconds (which happens, and is in
# the logs) gets ONE property sequence and no nudges — the nudge is for
# "they came back later", not for a burst. Anything past this window is a
# genuine second visit and does get a nudge, so the comment reply pointing
# at their inbox stays true. Deliberately short: the whole point of the fix
# is that a real re-comment produces a real DM, and a long cooldown would
# quietly recreate the original bug.
_NUDGE_COOLDOWN_SECONDS = 60

# Ceiling on the table below, evicted oldest-first. Losing an entry costs
# at most one extra nudge DM (the value is only ever compared against a
# 60-second cooldown), so this is the least consequential of the bounded
# tables — it simply must not be unbounded.
_MAX_LAST_DM_ENTRIES = 10_000

# dedupe_key -> monotonic time of the last DM sent for it. In memory only:
# it exists to smooth out a burst arriving within seconds, so surviving a
# restart buys nothing (the cost of losing it is at most one extra nudge).
# Only ever written while holding that person's lock in
# _maybe_send_property_sequence, and dedupe_key always embeds the user id,
# so the entries for one key are never touched by two threads at once.
_last_dm_at: "OrderedDict[str, float]" = OrderedDict()


def _remember_dm_time(dedupe_key: str, when: float) -> None:
    """Records when a DM went out for this key, keeping the table bounded.
    Called only while holding that person's own lock, exactly as the direct
    assignments it replaces were."""
    _last_dm_at[dedupe_key] = when
    _last_dm_at.move_to_end(dedupe_key)
    while len(_last_dm_at) > _MAX_LAST_DM_ENTRIES:
        _last_dm_at.popitem(last=False)


# Daily allowance buckets for this channel — see Middleware/daily_quota.py.
# Comments and DMs are counted SEPARATELY, on purpose: they are two
# different actions costing two different things, and someone who has been
# commenting on reels all morning should still be able to share one into
# our inbox.
_COMMENT_BUCKET = "instagram_comment"
_DM_BUCKET = "instagram_dm"


def _comment_limits() -> daily_quota.Limits:
    return daily_quota.Limits(max_messages=get_settings().instagram_daily_comment_limit)


def _dm_limits() -> daily_quota.Limits:
    return daily_quota.Limits(max_messages=get_settings().instagram_daily_dm_limit)


def _drop_over_quota(event_key: str, ig_user_id: str, what: str, first_refusal: bool) -> None:
    """The one way an over-allowance Instagram event leaves this module.

    Marking it ignored (memory-only, no write — see
    instagram_contact_store.mark_event_ignored) is what makes the drop
    actually free. Instagram hands back the same recent comments and
    messages on every 8-second cycle for as long as they are recent; an
    unmarked drop would be re-examined 450 times an hour, each time asking
    the database whether it had been handled. Marked, it is answered from
    memory from then on, and stays answered after the 6 AM reset — so a
    flood is refused once and costs nothing for the rest of its life.

    No DM is ever sent about this, unlike the WhatsApp side's one courtesy
    note. There is nobody to reassure here: an account at this volume on a
    public comment thread is not a customer mid-conversation, and a reply
    would hand a flood a way to make this account send messages.
    """
    instagram_contact_store.mark_event_ignored(event_key)
    if first_refusal:
        step_logger.warn(
            f"Instagram user {ig_user_id!r} has reached today's {what} allowance — further {what}s from "
            "this account are being ignored until 6 AM."
        )


def _lock_for_user(ig_user_id: str) -> threading.Lock:
    with _user_locks_guard:
        lock = _user_locks.get(ig_user_id)
        if lock is None:
            lock = threading.Lock()
            _user_locks[ig_user_id] = lock
        _user_locks.move_to_end(ig_user_id)
        if len(_user_locks) > _MAX_USER_LOCKS:
            # Oldest first, and HELD LOCKS ARE SKIPPED — see the comment on
            # _user_locks for why that skip is the part that matters. The
            # scan stops as soon as the table is back under its ceiling, and
            # the lock just handed out is never a candidate: it was moved to
            # the end a line ago, and the caller is about to acquire it.
            for key in list(_user_locks.keys()):
                if len(_user_locks) <= _MAX_USER_LOCKS:
                    break
                candidate = _user_locks[key]
                if candidate is not lock and not candidate.locked():
                    del _user_locks[key]
        return lock


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

    # The reels being watched, served from memory — see
    # instagram_reel_matcher's module docstring. This is a database read only
    # on the first cycle and after a property is actually added, edited or
    # deleted; an idle cycle reads nothing, which is what allows a serverless
    # database to stay suspended between real Instagram events.
    tracked_reels = instagram_reel_matcher.get_tracked_reels()

    # --- Phase 1: every independent read, issued together ------------------
    comment_fetches: list[tuple[EmbeddedProperty, str, Future]] = []
    for tracked in tracked_reels:
        media_pk = instagram_reel_matcher.resolve_media_pk(tracked)
        if media_pk is None:
            step_logger.warn(
                f"Instagram polling: property {tracked.prop.record_id!r} has a reel link but it couldn't be "
                "resolved yet — will retry next cycle. Check the link is a real, public reel URL."
            )
            continue
        comment_fetches.append(
            (tracked.prop, media_pk, pool.submit(_fetch_comments, client, tracked.prop, media_pk))
        )

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
    """Two independent stages, each with its own marker: the DM stage runs
    FIRST, then the public reply.

    The order matters and is the fix for "the reply came but the DM never
    did". The reply's only job is to point at the DM, so it must not be
    written until it's known what the DM stage actually did — and there are
    two entirely legitimate outcomes where no property sequence is sent:
    the person already converted to WhatsApp (linked_phone), or they've
    already been DMed about this exact property. Both used to return
    silently while the reply still went out saying "Plzz check your DM!",
    which is precisely the symptom: reply visible, inbox empty, nothing in
    the log to explain the gap.

    The two markers are separate on purpose. Sharing one meant a failure in
    either stage re-ran the other on the next cycle — a failing reply would
    re-trigger the DM stage every 8s.
    """
    commenter_id = str(comment.user.pk)
    comment_key = f"comment:{comment.pk}"
    dm_key = f"comment_dm:{comment.pk}"

    # Asked BEFORE the two lookups below, and that ordering is the point:
    # once an account is out of allowance, every further comment it has left
    # is dropped on an in-memory dictionary lookup, without the database
    # being asked anything at all. Reading the allowance consumes nothing,
    # so this cannot itself push anyone over.
    if daily_quota.is_exhausted(_COMMENT_BUCKET, commenter_id, _comment_limits()):
        _drop_over_quota(comment_key, commenter_id, "comment", first_refusal=False)
        instagram_contact_store.mark_event_ignored(dm_key)
        return

    replied = instagram_contact_store.is_event_processed(comment_key)
    dm_done = instagram_contact_store.is_event_processed(dm_key)
    if replied and dm_done:
        return  # fully handled in an earlier cycle; nothing to re-check

    if replied and not dm_done:
        # A comment recorded before dm_key existed. Under the old code the
        # DM stage ran (to completion or to a logged error) in the same
        # cycle the reply was marked, so backfill the marker instead of
        # re-running it — re-running would nudge people about comments from
        # days ago the moment this ships.
        instagram_contact_store.mark_event_processed(dm_key)
        return

    # Genuinely new, and this account still has allowance a moment ago —
    # so this is where the comment is actually booked against it. Counted
    # here rather than at the top so that re-seeing an already-handled
    # comment (which happens on every cycle until it ages out of
    # Instagram's own list) never spends anything.
    booking = daily_quota.consume(_COMMENT_BUCKET, commenter_id, _comment_limits())
    if not booking.allowed:
        _drop_over_quota(comment_key, commenter_id, "comment", first_refusal=booking.first_refusal)
        instagram_contact_store.mark_event_ignored(dm_key)
        return

    step_logger.info(
        f"Instagram polling: new comment {comment.pk!r} from @{comment.user.username} on property "
        f"{prop.record_id!r} — replying..."
    )

    outcome = _maybe_send_property_sequence(
        prop,
        ig_user_id=commenter_id,
        ig_username=comment.user.username,
        send=lambda text: instagram_messenger.send_dm_to_user(commenter_id, text),
        # Repeat comments from the same person on the same property share
        # one DM sequence, not one per comment — otherwise someone commenting
        # three times in a row gets the full sequence three times. They do
        # still get a short nudge DM (nudge_on_duplicate), so the reply is
        # never left pointing at an inbox nothing arrived in.
        dedupe_key=f"dm_sent:comment:{prop.record_id}:{commenter_id}",
        nudge_on_duplicate=True,
    )
    if outcome == "failed":
        # Nothing reached their inbox, so don't post a reply pointing at it
        # and don't mark either stage — the next cycle retries both.
        return
    instagram_contact_store.mark_event_processed(dm_key)

    reply_text = (
        templates.COMMENT_REPLY_ON_WHATSAPP_TEXT if outcome == "converted" else templates.COMMENT_REPLY_TEXT
    )
    if instagram_messenger.reply_to_comment(media_pk, int(comment.pk), reply_text):
        instagram_contact_store.mark_event_processed(comment_key)
        step_logger.success(f"Replied to Instagram comment {comment.pk} on property {prop.record_id!r}.")


def _handle_thread(thread) -> None:
    for message in thread.messages:
        if message.is_sent_by_viewer:
            continue  # our own outbound message, not something to react to

        message_key = f"dm_message:{message.id}"
        sender_id = str(message.user_id) if message.user_id else None

        # Same ordering, and the same reasoning, as the comment path: an
        # account already out of allowance is dropped on a dictionary
        # lookup, before the database is asked anything. Resolved up here
        # (it used to be read after the reel match) purely because the
        # allowance is keyed on the sender and has to be able to answer
        # first — a message with no sender id is still handled exactly as
        # it was, just a few lines earlier.
        if sender_id is not None and daily_quota.is_exhausted(_DM_BUCKET, sender_id, _dm_limits()):
            _drop_over_quota(message_key, sender_id, "DM", first_refusal=False)
            continue

        if instagram_contact_store.is_event_processed(message_key):
            continue

        if sender_id is None:
            instagram_contact_store.mark_event_ignored(message_key)
            continue

        # Booked before the reel is matched, so that EVERY new incoming
        # message counts — not just the ones that turn out to be a shared
        # reel. A message that matches nothing still costs a row written to
        # mark it handled, and an inbox filled with plain text would
        # otherwise be an unlimited supply of those.
        booking = daily_quota.consume(_DM_BUCKET, sender_id, _dm_limits())
        if not booking.allowed:
            _drop_over_quota(message_key, sender_id, "DM", first_refusal=booking.first_refusal)
            continue

        prop = _match_shared_reel(message)
        if prop is None:
            # Either not a reel share at all, or a reel share that matched
            # nothing we track — either way there's nothing to act on, and
            # no reason to look at this exact message again.
            #
            # Marked in memory rather than written to the database, and the
            # reason is cost: this is the branch EVERY ordinary DM takes —
            # a "hi", a thank-you, anything that is not a reel — and each
            # one used to write a row purely to record that it was of no
            # interest. Nothing was ever sent for it, so there is no
            # duplicate reply to protect against; the worst a restart can
            # do is have this message examined once more and reach the same
            # conclusion. (A message we ACT on is still written durably,
            # below, where a duplicate really would matter.)
            instagram_contact_store.mark_event_ignored(message_key)
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
        outcome = _maybe_send_property_sequence(
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
        if outcome != "failed":
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
    prop: EmbeddedProperty,
    *,
    ig_user_id: str,
    ig_username: Optional[str],
    send,
    dedupe_key: Optional[str],
    nudge_on_duplicate: bool = False,
) -> str:
    """Returns WHICH of the four outcomes happened, not just pass/fail:

      "sent"      — the full three-message sequence went out.
      "nudged"    — already DMed about this property, so a single short
                    nudge went out instead (nudge_on_duplicate only).
      "duplicate" — already DMed about this property, nothing sent.
      "converted" — they already gave a WhatsApp number; nothing is ever
                    sent to their Instagram inbox again.
      "failed"    — a genuine send failure. The ONLY value that means
                    "retry me": callers tracking their own idempotency
                    (see _handle_thread, _handle_comment) leave the event
                    unmarked so the next cycle picks it up again.

    This used to be a bare bool, with "sent", "duplicate" and "converted"
    all collapsed into True and logged nowhere. That is what made the
    reply-without-a-DM case invisible: the caller could not tell a DM that
    was sent from one that was deliberately skipped, so it always replied
    "check your DM", and the log recorded only the reply. Every branch now
    also logs, so the skip is visible in the terminal at the moment it
    happens.

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
            step_logger.info(
                f"Instagram user {ig_user_id!r} (@{ig_username or existing_contact.ig_username}) already gave "
                f"WhatsApp number {existing_contact.linked_phone} — no Instagram DM sent, the conversation "
                "continues on WhatsApp."
            )
            return "converted"

        if dedupe_key is not None and instagram_contact_store.is_event_processed(dedupe_key):
            if not nudge_on_duplicate:
                step_logger.info(
                    f"Instagram user {ig_user_id!r} (@{ig_username}) was already sent the DM sequence for "
                    f"property {prop.record_id!r} — not repeating it."
                )
                return "duplicate"
            since_last_dm = time.monotonic() - _last_dm_at.get(dedupe_key, float("-inf"))
            if since_last_dm < _NUDGE_COOLDOWN_SECONDS:
                step_logger.info(
                    f"Instagram user {ig_user_id!r} (@{ig_username}) was DMed about property {prop.record_id!r} "
                    f"{since_last_dm:.0f}s ago — replying to this comment without another DM."
                )
                return "duplicate"
            if not send(templates.DM_REPEAT_NUDGE_TEXT):
                step_logger.error(
                    f"Failed to send the Instagram nudge DM to user {ig_user_id!r} — will retry next poll."
                )
                return "failed"
            _remember_dm_time(dedupe_key, time.monotonic())
            step_logger.success(
                f"Instagram user {ig_user_id!r} (@{ig_username}) had already been sent property "
                f"{prop.record_id!r} — sent a short nudge DM instead of repeating the sequence."
            )
            return "nudged"

        token = form_token_service.issue_token(channel="instagram", identity=ig_user_id)
        form_link = f"{get_settings().inquiry_form_base_url.rstrip('/')}/{token}"

        sent = (
            send(templates.build_property_info_message(prop))
            and send(templates.build_site_visit_message())
            and send(templates.build_more_options_message(form_link))
        )
        if not sent:
            step_logger.error(
                f"Failed to send the full Instagram DM sequence to user {ig_user_id!r} — will retry next poll."
            )
            return "failed"

        if dedupe_key is not None:
            instagram_contact_store.mark_event_processed(dedupe_key)
            _remember_dm_time(dedupe_key, time.monotonic())
        instagram_contact_store.upsert_contact(
            existing_contact.model_copy(update={"ig_username": ig_username or existing_contact.ig_username})
            if existing_contact is not None
            else InstagramContactRecord(ig_user_id=ig_user_id, ig_username=ig_username, status="new")
        )
        step_logger.success(f"Sent Instagram DM sequence for property {prop.record_id!r} to user {ig_user_id!r}.")
        return "sent"
