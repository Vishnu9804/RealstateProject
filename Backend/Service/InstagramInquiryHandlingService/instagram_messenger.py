"""Every outbound action this feature takes on Instagram — the public
comment reply, the private reply that turns a comment into a DM, and an
ordinary DM inside an open conversation.

Mirrors Service/WhatsAppInquiryHandlingService/outbound_messenger.py's role
for the Instagram side: nothing outside this module builds an Instagram
request, so "not connected right now" and "that call failed" are handled in
exactly one place, and every failure is reported to
instagram_connection_service.note_api_error — which is what lets a revoked
token show up on the Connection page within seconds instead of silently
failing every event.

Three sending shapes, and WHICH ONE is used is not interchangeable — this
is the single most important constraint in the whole official-API rewrite:

  reply_to_comment          public reply under the comment. Always allowed.

  send_private_reply        the ONLY way to open a DM with someone who has
                            merely commented. Meta allows exactly ONE per
                            comment, within 7 days of it. That is why the
                            comment path sends one combined message rather
                            than the three separate DMs the old private-API
                            implementation could send: a second call for the
                            same comment is refused, so splitting the
                            sequence would deliver the first third and drop
                            the rest.

  send_dm_to_user           an ordinary DM. Only allowed while a 24-hour
                            messaging window is open, which is opened by the
                            PERSON messaging the account — so this is the
                            right call for someone who shared a reel into
                            the inbox (they just messaged us), and for the
                            confirmation that follows a form submission, but
                            it is NOT a substitute for a private reply.

Both DM shapes are capped at 1000 bytes per message by Meta, so text is
split on paragraph/word boundaries where more than one message is allowed —
see split_for_send.
"""

from __future__ import annotations

from typing import List

from Middleware import step_logger
from Service.InstagramInquiryHandlingService import instagram_connection_service
from Service.InstagramInquiryHandlingService import instagram_graph_client as graph
from Service.InstagramInquiryHandlingService.instagram_graph_client import InstagramApiError

# Meta's documented ceiling for one message's UTF-8 text. Counted in BYTES,
# not characters — these messages are full of emoji, each of which is four
# bytes, so a character count would quietly allow messages Instagram
# refuses.
MAX_MESSAGE_BYTES = 1000


def byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def split_for_send(text: str) -> List[str]:
    """Splits text into pieces that each fit inside MAX_MESSAGE_BYTES,
    preferring paragraph breaks, then line breaks, then word boundaries, and
    only cutting mid-word if a single word is somehow longer than the whole
    allowance. Returns [text] unchanged — the overwhelmingly common case —
    when it already fits."""
    if byte_length(text) <= MAX_MESSAGE_BYTES:
        return [text]

    pieces: List[str] = []
    remaining = text
    while byte_length(remaining) > MAX_MESSAGE_BYTES:
        head = _take_prefix(remaining, MAX_MESSAGE_BYTES)
        cut = max(head.rfind("\n\n"), head.rfind("\n"), head.rfind(" "))
        if cut <= 0:
            cut = len(head)
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def _take_prefix(text: str, max_bytes: int) -> str:
    """The longest prefix of `text` that encodes to at most max_bytes, never
    splitting a character in half."""
    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


# --- the three outbound actions -------------------------------------------


def reply_to_comment(comment_id: str, text: str) -> bool:
    """The public reply that appears under the comment."""
    token = instagram_connection_service.get_access_token()
    if token is None:
        step_logger.error(f"Cannot reply to Instagram comment {comment_id}: not connected.")
        return False
    try:
        graph.graph_post(f"{comment_id}/replies", token=token, params={"message": text})
        return True
    except InstagramApiError as exc:
        instagram_connection_service.note_api_error(exc, f"replying to comment {comment_id}")
        return False


def send_private_reply_to_comment(comment_id: str, text: str) -> bool:
    """Opens (or continues into) a DM with whoever left this comment.

    Only the FIRST message is deliverable — Meta refuses a second private
    reply for the same comment — so callers must pass everything they want
    that person to receive in one `text`. Anything longer than
    MAX_MESSAGE_BYTES is truncated on a boundary rather than rejected: a
    slightly shortened message reaching someone beats no message at all, and
    the caller (instagram_event_service) composes well inside the limit.
    """
    token = instagram_connection_service.get_access_token()
    if token is None:
        step_logger.error(f"Cannot send a private reply to Instagram comment {comment_id}: not connected.")
        return False

    body = text
    if byte_length(body) > MAX_MESSAGE_BYTES:
        body = _take_prefix(body, MAX_MESSAGE_BYTES)
        step_logger.warn(
            f"The private reply for Instagram comment {comment_id} was longer than Instagram's "
            f"{MAX_MESSAGE_BYTES}-byte limit and was shortened to fit."
        )
    try:
        graph.graph_post(
            "me/messages",
            token=token,
            json_body={"recipient": {"comment_id": str(comment_id)}, "message": {"text": body}},
        )
        return True
    except InstagramApiError as exc:
        instagram_connection_service.note_api_error(exc, f"private reply to comment {comment_id}")
        return False


def send_dm_to_user(ig_user_id: str, text: str) -> bool:
    """An ordinary DM to an Instagram-scoped user id, split across as many
    messages as the 1000-byte limit requires.

    Same name and signature as the previous (private-API) implementation on
    purpose: Service/WhatsAppInquiryHandlingService/inquiry_form_service.py
    calls this to confirm a submitted requirements form, and that call site
    is unchanged by the move to the official API.

    Returns False if ANY piece failed, so a caller that retries does not
    treat a half-delivered message as sent.
    """
    token = instagram_connection_service.get_access_token()
    if token is None:
        step_logger.error(f"Cannot DM Instagram user {ig_user_id}: not connected.")
        return False

    for piece in split_for_send(text):
        try:
            graph.graph_post(
                "me/messages",
                token=token,
                json_body={"recipient": {"id": str(ig_user_id)}, "message": {"text": piece}},
            )
        except InstagramApiError as exc:
            # The most likely cause by far is the 24-hour messaging window
            # having closed — this person has not messaged the account
            # recently, and Meta's rules simply do not allow an unsolicited
            # DM. Said plainly in the log, because it is a policy outcome,
            # not a bug to go hunting for.
            instagram_connection_service.note_api_error(exc, f"DM to Instagram user {ig_user_id}")
            step_logger.error(
                f"Failed to DM Instagram user {ig_user_id}: {exc}. If this says the conversation is "
                "outside the allowed window, it means they have not messaged this account in the last "
                "24 hours — Instagram does not permit a DM in that case."
            )
            return False
    return True
