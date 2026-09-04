"""Thin sending wrapper around the live instagrapi client — mirrors
Service/WhatsAppInquiryHandlingService/outbound_messenger.py's role for
Instagram: every place that needs to actually talk to Instagram (the
comment/DM poller, the shared inquiry form's confirmation message) goes
through here rather than reaching into instagram_connection_service.get_client()
itself, so "not connected right now" is handled in exactly one place.

Every failure here also calls instagram_connection_service.verify_session_now
— a send failing is just as strong a signal the session has gone bad as a
fetch failing (see instagram_polling_service.py's matching calls), and
without this a dead session would otherwise only be caught up to 6 hours
later by the periodic keepalive check, with every send in between failing
the same way and no visible change in the Connection tab's status.
"""

from __future__ import annotations

from Middleware import step_logger
from Service.InstagramInquiryHandlingService import instagram_connection_service


def reply_to_comment(media_id: str, comment_pk: int, text: str) -> bool:
    client = instagram_connection_service.get_client()
    if client is None:
        step_logger.error(f"Cannot reply to Instagram comment {comment_pk}: not connected.")
        return False
    try:
        client.media_comment(media_id, text, replied_to_comment_id=comment_pk)
        return True
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Failed to reply to Instagram comment {comment_pk} ({type(exc).__name__}): {exc}")
        instagram_connection_service.verify_session_now(f"comment reply failed: {type(exc).__name__}")
        return False


def send_dm_to_user(ig_user_id: str, text: str) -> bool:
    """Sends by user id, not thread id — instagrapi/Instagram resolve this
    to that user's existing 1:1 thread if one exists, so this is the right
    call whenever no thread_id is already in hand (e.g. the first message
    to a brand-new commenter, or the form's post-submit confirmation)."""
    client = instagram_connection_service.get_client()
    if client is None:
        step_logger.error(f"Cannot DM Instagram user {ig_user_id}: not connected.")
        return False
    try:
        client.direct_send(text, user_ids=[int(ig_user_id)])
        return True
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Failed to DM Instagram user {ig_user_id} ({type(exc).__name__}): {exc}")
        instagram_connection_service.verify_session_now(f"DM to user failed: {type(exc).__name__}")
        return False


def send_dm_to_thread(thread_id: int, text: str) -> bool:
    """Sends into an already-known thread — used when the poller found the
    trigger (a shared reel) by scanning an existing thread, so replying
    into that same thread is both simpler and keeps the conversation in
    one place rather than risking a second thread."""
    client = instagram_connection_service.get_client()
    if client is None:
        step_logger.error(f"Cannot DM Instagram thread {thread_id}: not connected.")
        return False
    try:
        client.direct_answer(thread_id, text)
        return True
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Failed to DM Instagram thread {thread_id} ({type(exc).__name__}): {exc}")
        instagram_connection_service.verify_session_now(f"DM to thread failed: {type(exc).__name__}")
        return False
