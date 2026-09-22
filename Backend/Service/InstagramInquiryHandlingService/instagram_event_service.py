"""Turns a webhook delivery from Meta into the same replies this feature
has always sent — the public comment reply, the DM with the property's
details, and the requirements-form link.

This module replaces instagram_polling_service.py entirely. The behaviour it
produces is the same; how the work ARRIVES is the whole change:

  before   a background thread asked Instagram "any new comments? any new
           DMs?" every 8 seconds, forever. On an account with nothing
           happening that was still ~10,000 rounds of API calls a day, each
           one touching the property snapshot and, on a miss, the database —
           and, because it drove a normal account through the private mobile
           API, the thing that made Instagram start warning about automated
           activity in the first place.

  now      nothing runs until Meta posts an event. An idle account costs
           exactly zero CPU, zero database traffic and zero API calls. A
           real comment is handled in the second it happens rather than up
           to 8 seconds later.

The three invariants that made the old concurrent poller safe are kept,
because they protect against things webhooks make MORE likely, not less:

  - Every job is wrapped in _guarded, so one failing event can never affect
    another.
  - All work for a GIVEN person is serialised behind that person's own lock,
    so two events arriving together cannot both pass the same "already
    sent?" check. Different people are still handled at the same time.
  - Every event is idempotent on its own id (comment id / message id), and
    an event is only marked handled once something was actually delivered.
    Meta re-delivers a webhook it did not get a prompt 200 for, so this is
    not a theoretical concern: it is the normal way duplicates arrive.

One genuine behavioural difference, forced by Meta's rules rather than
chosen: a comment can only ever produce ONE direct message (a "private
reply", allowed once per comment, within 7 days). The three-message DM
sequence therefore goes out as one combined message on the comment path.
Every word of it is still sent, and the DM path — someone sharing a reel
into the inbox, which opens a 24-hour messaging window — still sends the
three separate messages exactly as before.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from Config.settings import get_settings
from Middleware import daily_quota, step_logger
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.BackendUsageService import cpu_usage_service
from Service.InstagramInquiryHandlingService import (
    instagram_connection_service,
    instagram_contact_store,
    instagram_message_templates as templates,
    instagram_messenger,
    instagram_reel_matcher,
)
from Service.WhatsAppInquiryHandlingService import form_token_service

# How many events may be handled at once.
#
# Small on purpose, and for a different reason than the old poller's limit:
# there is no account-safety argument any more (this is the sanctioned API),
# only a hosting one. Each worker is a thread doing short bursts of network
# I/O, and the deployment target is a small Railway instance where threads
# that exist all day for occasional work are pure overhead. Three covers
# several people acting in the same second comfortably; anything beyond that
# queues for a fraction of a second rather than being dropped.
#
# The pool is created LAZILY, on the first real event, so an instance that
# never receives a webhook never creates a thread at all.
_MAX_WORKERS = 3
_pool_lock = threading.Lock()
_pool: Optional[ThreadPoolExecutor] = None

# Attachment kinds that can carry a shared post/reel. Anything else (an
# image someone sent, a story mention, an audio clip) is not a share of our
# content and is ignored without a lookup.
_SHARE_ATTACHMENT_TYPES = frozenset({"ig_reel", "reel", "share", "ig_post", "post"})

# --- the bounded tables carried over from the poller ----------------------

_MAX_USER_LOCKS = 10_000
_user_locks_guard = threading.Lock()
_user_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()

# Shortest gap between two DMs to the same person about the same property.
# Someone commenting four times in twenty seconds gets ONE property sequence
# and no nudges; anything past this window is a genuine second visit and does
# get a nudge, so the comment reply pointing at their inbox stays true.
_NUDGE_COOLDOWN_SECONDS = 60
_MAX_LAST_DM_ENTRIES = 10_000
_last_dm_at: "OrderedDict[str, float]" = OrderedDict()

# Event ids currently being handled, so two concurrent deliveries of the
# SAME event cannot both get past the "already handled?" check.
#
# This is not redundant with the per-person lock. Meta re-delivers an event
# it did not receive a prompt 200 for, and a re-delivery can land while the
# first copy is still mid-sequence — at which point both copies are for the
# same person, so the person lock serialises them, and the second then runs
# the whole sequence again because the first had not finished marking it.
# This set is what makes the second copy a no-op instead.
_inflight_guard = threading.Lock()
_inflight: set = set()

_COMMENT_BUCKET = "instagram_comment"
_DM_BUCKET = "instagram_dm"


def _comment_limits() -> daily_quota.Limits:
    return daily_quota.Limits(max_messages=get_settings().instagram_daily_comment_limit)


def _dm_limits() -> daily_quota.Limits:
    return daily_quota.Limits(max_messages=get_settings().instagram_daily_dm_limit)


def _pool_handle() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="ig-event")
        return _pool


def enqueue_webhook(payload: dict) -> None:
    """Hands a verified webhook body to a worker and returns immediately.

    Returning fast is not a nicety: Meta expects a 200 within seconds and
    re-delivers anything slower, so doing the sending work inline would turn
    every slow Instagram call into a duplicate delivery.
    """
    _pool_handle().submit(_guarded, _process_payload, "webhook payload", payload)


def _guarded(func, description: str, *args) -> None:
    """Per-event error isolation — one failing event can never affect
    another, and the log names the specific event rather than reading as a
    generic failure."""
    try:
        func(*args)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Instagram: handling {description} failed ({type(exc).__name__}): {exc!r}")


@cpu_usage_service.tracked("Instagram webhook event", "Instagram")
def _process_payload(payload: dict) -> None:
    """Fans one delivery out into one job per event it contains.

    Meta batches: a single POST can carry several entries, and an entry can
    carry several changes or several messages. Each is submitted separately
    so that two people who acted at the same moment are served at the same
    moment.
    """
    if not isinstance(payload, dict):
        return
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        # Both comments AND messages arrive as entry.changes[] on THIS
        # product — "API setup with Instagram business login" — confirmed
        # directly against the Meta App Dashboard's own "Send to My Server"
        # sample for the messages field, which is a bare {"field":
        # "messages", "value": {sender, recipient, message, ...}} pair, the
        # same shape as the comments field, NOT the Messenger-Platform-style
        # entry.messaging[] array. That array belongs to Facebook Login for
        # Business (a Page-linked Instagram account) — a different product
        # this app does not use.
        #
        # This is the one thing worth stating plainly: earlier code here
        # only ever dispatched field == "comments" and silently dropped
        # every "messages" change — so every DM and every shared reel was
        # received, acknowledged with 200, and then discarded without a
        # single log line. It looked exactly like "nothing arrived" from the
        # terminal, which is what made it so easy to misdiagnose as a
        # tester-role problem instead. Fixed by routing both fields.
        #
        # entry.messaging[] is still checked below too, and unconditionally
        # so — a bare field/value pair also on the entry itself is a shape
        # Meta uses on some app types, and checking a key that isn't there
        # costs nothing.
        changes = entry.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    _dispatch_change(change)
        if entry.get("field"):
            _dispatch_change(entry)

        messaging = entry.get("messaging")
        if isinstance(messaging, list):
            for event in messaging:
                if isinstance(event, dict):
                    _pool_handle().submit(_guarded, _handle_message_event, "an Instagram DM", event)


def _dispatch_change(change: dict) -> None:
    field = change.get("field")
    value = change.get("value")
    if not isinstance(value, dict):
        return
    if field == "comments":
        _pool_handle().submit(
            _guarded, _handle_comment_event, f"Instagram comment {value.get('id')}", value
        )
    elif field == "messages":
        # `value` here is already shaped exactly like an entry.messaging[]
        # element (sender/recipient/message) — see the module-level comment
        # above — so it goes straight to the same handler, no translation
        # needed.
        mid = (value.get("message") or {}).get("mid") if isinstance(value.get("message"), dict) else None
        _pool_handle().submit(_guarded, _handle_message_event, f"Instagram message {mid}", value)


# --- comments -------------------------------------------------------------


@cpu_usage_service.tracked("Instagram webhook event", "Instagram")
def _handle_comment_event(value: dict) -> None:
    comment_id = value.get("id")
    media = value.get("media") if isinstance(value.get("media"), dict) else {}
    media_id = media.get("id")
    author = value.get("from") if isinstance(value.get("from"), dict) else {}
    commenter_id = str(author.get("id")) if author.get("id") else None
    commenter_username = author.get("username")

    if not comment_id or not media_id:
        return

    # THE loop guard. Our own public reply to a comment comes straight back
    # as another `comments` notification; without this the handler would
    # answer its own reply forever. The username check is a second line of
    # defence for the case where Meta reports the account under an id this
    # process has not seen (app-scoped vs professional-account id).
    if instagram_connection_service.is_self_id(commenter_id) or (
        commenter_username
        and commenter_username == instagram_connection_service.get_status().get("username")
    ):
        return

    if commenter_id is None:
        step_logger.warn(
            f"Instagram comment {comment_id} arrived without an author id — nothing can be sent to "
            "someone who cannot be identified, so it was ignored."
        )
        return

    comment_key = f"comment:{comment_id}"
    dm_key = f"comment_dm:{comment_id}"

    # Asked before anything else that costs, and that ordering is the point:
    # once an account is out of allowance, every further comment it leaves is
    # dropped on an in-memory dictionary lookup, with no database query and
    # no Instagram call at all.
    if daily_quota.is_exhausted(_COMMENT_BUCKET, commenter_id, _comment_limits()):
        _drop_over_quota(comment_key, commenter_id, "comment", first_refusal=False)
        instagram_contact_store.mark_event_ignored(dm_key)
        return

    if not _begin_event(comment_key):
        return  # a duplicate delivery of this exact comment is already in flight
    try:
        # "Is this comment even about a property?" is asked FIRST, before the
        # idempotency check, and the ordering is a deliberate database-cost
        # decision rather than a stylistic one.
        #
        # Meta sends a notification for EVERY comment on EVERY post the
        # account has — an ordinary photo on the client's feed included.
        # Asking "have we handled this comment id before?" first would mean
        # one database lookup for each of those, forever, on a serverless
        # database billed for the time it stays awake. Asking the matcher
        # first answers them entirely from memory: instagram_reel_matcher
        # remembers which media are irrelevant, so the second comment on an
        # unrelated post costs nothing at all.
        prop = instagram_reel_matcher.find_property_by_media_id(str(media_id))
        if prop is None:
            # Not logged by default — this is genuinely the common case
            # (every comment on every ordinary post the account has), and an
            # INFO line per one of those would drown out everything else in
            # the terminal. media_product_type is printed at DEBUG-equivalent
            # cost (a warn only during initial setup would be too noisy
            # here, unlike the DM side where a share is rare) — left as a
            # deliberate asymmetry with _handle_message_event, not an
            # oversight: see that function's _looks_like_a_share for why a
            # DM share gets a log line and an ordinary comment does not.
            #
            # Marked in memory only (no row written) so this exact comment is
            # never reconsidered either — nothing was sent for it, so there
            # is no duplicate reply to protect against.
            instagram_contact_store.mark_event_ignored(comment_key)
            instagram_contact_store.mark_event_ignored(dm_key)
            return

        replied = instagram_contact_store.is_event_processed(comment_key)
        dm_done = instagram_contact_store.is_event_processed(dm_key)
        if replied and dm_done:
            return
        if replied and not dm_done:
            # Recorded by an older build that had a single marker. Backfill
            # rather than re-run: re-running would DM people about comments
            # from days ago the moment this ships.
            instagram_contact_store.mark_event_processed(dm_key)
            return

        booking = daily_quota.consume(_COMMENT_BUCKET, commenter_id, _comment_limits())
        if not booking.allowed:
            _drop_over_quota(comment_key, commenter_id, "comment", first_refusal=booking.first_refusal)
            instagram_contact_store.mark_event_ignored(dm_key)
            return

        step_logger.info(
            f"Instagram: new comment {comment_id} from @{commenter_username or commenter_id} on property "
            f"{prop.record_id!r} — replying..."
        )

        # One combined private reply, because Meta allows exactly one per
        # comment. See this module's docstring.
        outcome = _maybe_send_property_sequence(
            prop,
            ig_user_id=commenter_id,
            ig_username=commenter_username,
            send_sequence=lambda messages: instagram_messenger.send_private_reply_to_comment(
                str(comment_id), _combine_for_single_message(messages)
            ),
            # Repeat comments from the same person on the same property share
            # one DM sequence, not one per comment. They still get a short
            # nudge, so the public reply is never left pointing at an inbox
            # nothing arrived in.
            dedupe_key=f"dm_sent:comment:{prop.record_id}:{commenter_id}",
            nudge_on_duplicate=True,
        )
        if outcome == "failed":
            # Nothing reached their inbox, so don't post a public reply
            # pointing at it, and don't mark either stage — Meta will
            # re-deliver, or the next comment will retry.
            return
        instagram_contact_store.mark_event_processed(dm_key)

        reply_text = (
            templates.COMMENT_REPLY_ON_WHATSAPP_TEXT if outcome == "converted" else templates.COMMENT_REPLY_TEXT
        )
        if instagram_messenger.reply_to_comment(str(comment_id), reply_text):
            instagram_contact_store.mark_event_processed(comment_key)
            step_logger.success(f"Replied to Instagram comment {comment_id} on property {prop.record_id!r}.")
    finally:
        _end_event(comment_key)


# --- direct messages ------------------------------------------------------


@cpu_usage_service.tracked("Instagram webhook event", "Instagram")
def _handle_message_event(event: dict) -> None:
    message = event.get("message")
    if not isinstance(message, dict):
        return  # a reaction, a read receipt, a postback — nothing to answer
    if message.get("is_echo") or message.get("is_deleted"):
        return  # our own outbound message coming back, or one that was unsent

    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = str(sender.get("id")) if sender.get("id") else None
    if sender_id is None or instagram_connection_service.is_self_id(sender_id):
        return

    message_id = message.get("mid")
    if not message_id:
        return
    message_key = f"dm_message:{message_id}"

    # Same ordering, same reasoning as the comment path: an account already
    # out of allowance is dropped on a dictionary lookup, before the database
    # or Instagram is asked anything.
    if daily_quota.is_exhausted(_DM_BUCKET, sender_id, _dm_limits()):
        _drop_over_quota(message_key, sender_id, "DM", first_refusal=False)
        return

    if not _begin_event(message_key):
        return
    try:
        # Matched BEFORE anything that touches the database, for the same
        # reason as the comment path above: this is the branch every
        # ordinary DM takes — a "hi", a thank-you, an emoji — and Meta
        # notifies us about all of them. Matching is pure memory (an
        # attachment's permalink, or a link in the text), so a conversation
        # that is not about a property costs no query and writes no row.
        prop = _match_shared_reel(message)
        if prop is None:
            if _looks_like_a_share(message):
                # This is the one "no match" outcome worth a log line despite
                # the cost discipline everywhere else in this module: an
                # ordinary "hi" is silent because there is nothing to
                # diagnose, but something that LOOKED like a content share
                # and still matched no property is either a genuinely
                # unrelated post/reel or a payload shape this matcher
                # doesn't yet recognise — both worth seeing while setting
                # this up. Printed shape only (types and payload keys, never
                # message text), so it costs a terminal line, not a query.
                step_logger.info(
                    f"Instagram: a shared post/reel from user {sender_id} matched no tracked property "
                    f"— {_describe_attachments(message)}"
                )
            instagram_contact_store.mark_event_ignored(message_key)
            return

        if instagram_contact_store.is_event_processed(message_key):
            return

        # Booked only for a share we are actually going to answer. The
        # allowance exists to bound what one account can cost this backend,
        # and an unmatched message now costs nothing — so spending someone's
        # allowance on their small talk would only mean turning down the
        # genuine reel share they send afterwards.
        booking = daily_quota.consume(_DM_BUCKET, sender_id, _dm_limits())
        if not booking.allowed:
            _drop_over_quota(message_key, sender_id, "DM", first_refusal=booking.first_refusal)
            return

        step_logger.info(
            f"Instagram: shared reel from user {sender_id} matches property {prop.record_id!r} — replying..."
        )

        outcome = _maybe_send_property_sequence(
            prop,
            ig_user_id=sender_id,
            ig_username=None,
            # A person who just messaged us has an open 24-hour window, so
            # the three messages go out exactly as they always have.
            send_sequence=lambda messages: all(
                instagram_messenger.send_dm_to_user(sender_id, text) for text in messages
            ),
            # No dedupe_key, deliberately: idempotency here is keyed on the
            # message id above, which is unique per share. Reusing the
            # comment path's per-(property, person) key would silently skip a
            # genuine new share from someone already DMed about this property
            # — sharing is its own deliberate action and should always get a
            # reply.
            dedupe_key=None,
        )
        if outcome != "failed":
            instagram_contact_store.mark_event_processed(message_key)
    finally:
        _end_event(message_key)


def _looks_like_a_share(message: dict) -> bool:
    """True for anything that carries real content to match against — a
    recognised attachment type, ANY attachment at all (even one this
    matcher doesn't recognise — that's exactly the case worth logging), or a
    link pasted as plain text. False for a bare "hi", which is the
    overwhelming majority of DMs and must stay silent."""
    attachments = message.get("attachments")
    if isinstance(attachments, list) and attachments:
        return True
    return bool(instagram_reel_matcher.extract_reel_code(message.get("text")))


def _describe_attachments(message: dict) -> str:
    """Shape only — attachment types and each payload's key names, never the
    values (a permalink is fine to print; a lookaside CDN token or message
    text is not something this log line needs). Enough to tell "Meta sent a
    type this matcher's _SHARE_ATTACHMENT_TYPES doesn't include" apart from
    "Meta sent a type it does include, but pointing at unrelated content"."""
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        text = message.get("text")
        return f"no attachment; text={text!r}" if text else "no attachment, no text"
    parts = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else {}
        parts.append(f"type={attachment.get('type')!r} payload_keys={sorted(payload.keys())}")
    return "; ".join(parts) if parts else "an attachment list with no readable entries"


def _match_shared_reel(message: dict) -> Optional[EmbeddedProperty]:
    """A reel shared into DM arrives in one of three shapes, and all three
    are handled because which one Instagram sends depends on the sender's
    app version:

      - an attachment whose payload.url is the reel's own permalink, which
        carries the short code and needs no API call at all;
      - an attachment whose payload.url is a CDN/lookaside link (no short
        code) plus payload.reel_video_id, the media's numeric id — resolved
        to a permalink once and then cached forever;
      - no attachment at all, just the link pasted as text.
    """
    attachments = message.get("attachments")
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if str(attachment.get("type") or "").lower() not in _SHARE_ATTACHMENT_TYPES:
                continue
            payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else {}

            prop = instagram_reel_matcher.find_property_by_url(payload.get("url"))
            if prop is not None:
                return prop

            media_id = payload.get("reel_video_id") or payload.get("id") or payload.get("media_id")
            if media_id:
                prop = instagram_reel_matcher.find_property_by_media_id(str(media_id))
                if prop is not None:
                    return prop

    return instagram_reel_matcher.find_property_by_url(message.get("text"))


# --- the shared sending sequence -----------------------------------------


def _combine_for_single_message(messages) -> str:
    """The three-message sequence as ONE message, for the private-reply path.

    Kept whole wherever it fits, which for a real property it comfortably
    does (~700 bytes against Instagram's 1000-byte ceiling). If a long
    property somehow pushes it over, the site-visit line is the one dropped:
    the details and the requirements-form link are what the person came for,
    and the phone number is also in the public reply's follow-up
    conversation.
    """
    messages = [text for text in messages if text]
    joined = "\n\n".join(messages)
    if instagram_messenger.byte_length(joined) <= instagram_messenger.MAX_MESSAGE_BYTES:
        return joined
    if len(messages) > 2:
        trimmed = "\n\n".join([messages[0], messages[-1]])
        step_logger.warn(
            "The combined Instagram private reply was over Instagram's 1000-byte limit — the site-visit "
            "line was left out so the property details and the requirements link both fit."
        )
        return trimmed
    return joined


def _maybe_send_property_sequence(
    prop: EmbeddedProperty,
    *,
    ig_user_id: str,
    ig_username: Optional[str],
    send_sequence,
    dedupe_key: Optional[str],
    nudge_on_duplicate: bool = False,
) -> str:
    """Returns WHICH of the five outcomes happened, not just pass/fail:

      "sent"      — the full sequence went out.
      "nudged"    — already DMed about this property, so a single short
                    nudge went out instead (nudge_on_duplicate only).
      "duplicate" — already DMed about this property, nothing sent.
      "converted" — they already gave a WhatsApp number; nothing is ever
                    sent to their Instagram inbox again.
      "failed"    — a genuine send failure. The ONLY value that means
                    "retry me": callers leave the event unmarked so a
                    re-delivery picks it up again.

    Held under this person's own lock for its whole duration. Two events
    from the same person (two comments, or a comment and a share arriving
    together) would otherwise both pass the "already sent?" check before
    either had marked it, and both send. Different people never contend for
    this lock, so events from different people are still handled at the same
    time.
    """
    with _lock_for_user(ig_user_id):
        existing_contact = instagram_contact_store.get_contact(ig_user_id)
        if existing_contact is not None and existing_contact.linked_phone:
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
            if not send_sequence([templates.DM_REPEAT_NUDGE_TEXT]):
                step_logger.error(f"Failed to send the Instagram nudge DM to user {ig_user_id!r}.")
                return "failed"
            _remember_dm_time(dedupe_key, time.monotonic())
            step_logger.success(
                f"Instagram user {ig_user_id!r} (@{ig_username}) had already been sent property "
                f"{prop.record_id!r} — sent a short nudge DM instead of repeating the sequence."
            )
            return "nudged"

        token = form_token_service.issue_token(channel="instagram", identity=ig_user_id)
        form_link = f"{get_settings().inquiry_form_base_url.rstrip('/')}/{token}"

        if not send_sequence(
            [
                templates.build_property_info_message(prop),
                templates.build_site_visit_message(),
                templates.build_more_options_message(form_link),
            ]
        ):
            step_logger.error(
                f"Failed to send the Instagram DM sequence to user {ig_user_id!r} — nothing was marked "
                "handled, so a re-delivery or a further comment will try again."
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


# --- the small bounded tables --------------------------------------------


def _begin_event(event_key: str) -> bool:
    with _inflight_guard:
        if event_key in _inflight:
            return False
        _inflight.add(event_key)
        return True


def _end_event(event_key: str) -> None:
    with _inflight_guard:
        _inflight.discard(event_key)


def _remember_dm_time(dedupe_key: str, when: float) -> None:
    """Called only while holding that person's own lock."""
    _last_dm_at[dedupe_key] = when
    _last_dm_at.move_to_end(dedupe_key)
    while len(_last_dm_at) > _MAX_LAST_DM_ENTRIES:
        _last_dm_at.popitem(last=False)


def _lock_for_user(ig_user_id: str) -> threading.Lock:
    """One lock per Instagram user id, created on demand and bounded.

    Eviction is safe ONLY because a lock that is currently HELD is never
    removed. Dropping a held lock would hand the next caller a brand new one
    and let two sequences run for the same person at once — precisely the
    race this table exists to prevent — so that check is the load-bearing
    line, not the ceiling.
    """
    with _user_locks_guard:
        lock = _user_locks.get(ig_user_id)
        if lock is None:
            lock = threading.Lock()
            _user_locks[ig_user_id] = lock
        _user_locks.move_to_end(ig_user_id)
        if len(_user_locks) > _MAX_USER_LOCKS:
            for key in list(_user_locks.keys()):
                if len(_user_locks) <= _MAX_USER_LOCKS:
                    break
                candidate = _user_locks[key]
                if candidate is not lock and not candidate.locked():
                    del _user_locks[key]
        return lock


def _drop_over_quota(event_key: str, ig_user_id: str, what: str, first_refusal: bool) -> None:
    """The one way an over-allowance Instagram event leaves this module.

    Marking it ignored (memory-only, no write — see
    instagram_contact_store.mark_event_ignored) is what makes the drop
    actually free: a re-delivery of the same event is then answered from
    memory instead of costing a database lookup.

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
