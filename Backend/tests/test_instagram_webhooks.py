"""What the Instagram integration does with a webhook delivery, stated as
tests.

These are the RULES a person would recognise, not the plumbing: "a comment on
a property's reel gets one DM and one public reply", "our own reply must
never trigger another reply", "the same notification delivered twice sends
one set of messages". Meta re-delivers anything this server does not
acknowledge promptly, and our own public reply arrives back as another
comment notification, so the two things most worth protecting are
idempotency and the self-reply loop guard — a regression in either is not a
cosmetic bug, it is either a duplicate message to a customer or an infinite
loop of the account answering itself.

The handlers are called directly rather than through the HTTP route, so
nothing here depends on thread timing. The HTTP layer it skips (signature
verification) is covered separately at the bottom.

Run from the Backend directory:

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import hashlib
import hmac
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from Controller.InstagramInquiryHandlingController import instagram_webhook_controller as webhook_controller
from Middleware import daily_quota
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.InstagramInquiryHandlingService import (
    instagram_contact_store,
    instagram_event_service as events,
    instagram_message_templates as templates,
    instagram_messenger as messenger,
    instagram_reel_matcher as matcher,
)

OUR_IG_ID = "17841400000000001"
OUR_ACCOUNT_ID = "9999999999"
REEL_CODE = "DC4P0w1ilgK"
REEL_URL = f"https://www.instagram.com/reel/{REEL_CODE}/"
MEDIA_ID = "18001112223334445"
OTHER_MEDIA_ID = "18009998887776665"


def make_property(**overrides) -> EmbeddedProperty:
    fields = {
        "record_id": "prop-1",
        "source_message_id": "msg-1",
        "property_type": "Flat",
        "bhk": "3 BHK",
        "area_name": "Vesu",
        "area_sqft": 1800.0,
        "price_text": "1.25 cr",
        "instagram_reel_url": REEL_URL,
        "group_name": "Test Group",
        "chat_type": "group",
        "sender_name": "Sender",
        "sender_saved_name": "Sender",
        "sender_phone": "+919000000000",
        "message_text": "listing",
        "message_timestamp": datetime.now(timezone.utc),
        "embedding": [1.0, 0.0],
        "embedding_model": "test",
    }
    fields.update(overrides)
    return EmbeddedProperty(**fields)


PROP = make_property()


def comment_event(comment_id: str, *, from_id: str = "7777777777", media_id: str = MEDIA_ID) -> dict:
    return {
        "from": {"id": from_id, "username": f"user_{from_id}"},
        "media": {"id": media_id, "media_product_type": "REELS"},
        "id": comment_id,
        "text": "price?",
    }


def message_event(mid: str, *, sender_id: str = "5555555555", **message_fields) -> dict:
    message = {"mid": mid}
    message.update(message_fields)
    return {
        "sender": {"id": sender_id},
        "recipient": {"id": OUR_IG_ID},
        "timestamp": 1700000000,
        "message": message,
    }


class InstagramWebhookTestCase(unittest.TestCase):
    """Shared setup: a connected account, one reel-linked property, and a
    recording stand-in for every outbound Instagram call."""

    def setUp(self) -> None:
        # FIRST, before anything else: pin the contact store to its
        # in-memory backend for the whole test.
        #
        # Without this, a developer machine whose Backend/.env has a real
        # DATABASE_URL would run these tests against the live database —
        # and they do not only read it, they INSERT (every handled event
        # writes an idempotency row, every answered person writes a contact).
        # A test suite that quietly writes rows into production is a worse
        # problem than any bug it could catch.
        db_off = mock.patch.object(instagram_contact_store, "is_client_database_configured", lambda: False)
        db_off.start()
        self.addCleanup(db_off.stop)

        # Every bounded table this feature keeps in memory, emptied — these
        # are module-level by design (they exist to survive between events),
        # so without this each test would inherit the previous one's
        # "already handled" answers.
        instagram_contact_store._contacts.clear()
        instagram_contact_store._processed_events.clear()
        matcher._media_code.clear()
        matcher._media_unresolvable.clear()
        events._last_dm_at.clear()
        events._inflight.clear()
        daily_quota.reset_all()

        self.sent_dms: list[tuple[str, str]] = []
        self.private_replies: list[tuple[str, str]] = []
        self.public_replies: list[tuple[str, str]] = []
        self.permalink_lookups: list[str] = []

        patches = [
            mock.patch.object(
                matcher.property_vector_store, "get_recent_instagram_reel_properties", lambda limit: [(PROP, None)]
            ),
            mock.patch.object(matcher.property_vector_store, "get_reel_link_index", lambda: []),
            mock.patch.object(matcher, "shortcode_for_media_id", self._fake_shortcode),
            mock.patch.object(events.instagram_connection_service, "is_self_id", self._fake_is_self),
            mock.patch.object(
                events.instagram_connection_service, "get_status", lambda: {"username": "ourbiz"}
            ),
            mock.patch.object(events.instagram_messenger, "send_dm_to_user", self._fake_send_dm),
            mock.patch.object(
                events.instagram_messenger, "send_private_reply_to_comment", self._fake_private_reply
            ),
            mock.patch.object(events.instagram_messenger, "reply_to_comment", self._fake_public_reply),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    # --- stand-ins -------------------------------------------------------

    def _fake_shortcode(self, media_id: str):
        self.permalink_lookups.append(str(media_id))
        return REEL_CODE if str(media_id) == MEDIA_ID else None

    def _fake_is_self(self, candidate):
        return str(candidate) in {OUR_IG_ID, OUR_ACCOUNT_ID}

    def _fake_send_dm(self, ig_user_id: str, text: str) -> bool:
        self.sent_dms.append((ig_user_id, text))
        return True

    def _fake_private_reply(self, comment_id: str, text: str) -> bool:
        self.private_replies.append((comment_id, text))
        return True

    def _fake_public_reply(self, comment_id: str, text: str) -> bool:
        self.public_replies.append((comment_id, text))
        return True


class CommentRules(InstagramWebhookTestCase):
    def test_a_comment_on_a_property_reel_gets_one_dm_and_one_public_reply(self):
        events._handle_comment_event(comment_event("c1"))
        self.assertEqual(len(self.private_replies), 1)
        self.assertEqual(len(self.public_replies), 1)
        self.assertEqual(self.public_replies[0][1], templates.COMMENT_REPLY_TEXT)

    def test_the_single_dm_carries_the_whole_two_message_sequence(self):
        events._handle_comment_event(comment_event("c1"))
        text = self.private_replies[0][1]
        # Meta allows exactly one private reply per comment, so both
        # messages have to travel in this one.
        self.assertIn("Vesu", text)
        self.assertIn("1.25 cr", text)
        self.assertIn("Tell us your requirement by clicking on this link:", text)
        header, _, link = text[text.index("Tell us your requirement") :].partition("\n")
        self.assertEqual(header, "Tell us your requirement by clicking on this link:")
        self.assertTrue(link.strip() and " " not in link.strip())  # the link is right there, in this message
        self.assertLessEqual(messenger.byte_length(text), messenger.MAX_MESSAGE_BYTES)
        # The retired call-us / site-visit / more-options wording is gone.
        self.assertNotIn("site visit", text)
        self.assertNotIn("more options", text)

    def test_our_own_comment_never_triggers_a_reply(self):
        # Our public reply comes back as another comments notification; if
        # this ever fires, the account answers itself forever.
        events._handle_comment_event(comment_event("c-own", from_id=OUR_IG_ID))
        self.assertEqual(self.private_replies, [])
        self.assertEqual(self.public_replies, [])

    def test_a_comment_on_a_post_that_is_not_a_property_is_ignored(self):
        events._handle_comment_event(comment_event("cx", media_id=OTHER_MEDIA_ID))
        self.assertEqual(self.private_replies, [])
        self.assertEqual(self.public_replies, [])

    def test_a_redelivered_comment_sends_nothing_a_second_time(self):
        event = comment_event("c1")
        events._handle_comment_event(event)
        events._handle_comment_event(event)
        self.assertEqual(len(self.private_replies), 1)
        self.assertEqual(len(self.public_replies), 1)

    def test_a_second_comment_soon_after_gets_a_reply_but_no_second_dm(self):
        events._handle_comment_event(comment_event("c1"))
        events._handle_comment_event(comment_event("c2"))
        self.assertEqual(len(self.private_replies), 1)
        self.assertEqual(len(self.public_replies), 2)

    def test_a_second_comment_after_the_cooldown_gets_a_short_nudge(self):
        events._handle_comment_event(comment_event("c1"))
        # Age the record past the nudge cooldown rather than waiting it out.
        for key in list(events._last_dm_at):
            events._last_dm_at[key] -= events._NUDGE_COOLDOWN_SECONDS + 1
        events._handle_comment_event(comment_event("c2"))
        self.assertEqual(len(self.private_replies), 2)
        self.assertEqual(self.private_replies[1][1], templates.DM_REPEAT_NUDGE_TEXT)

    def test_someone_who_moved_to_whatsapp_is_told_so_and_never_dmed(self):
        instagram_contact_store.upsert_contact(
            InstagramContactRecord(ig_user_id="7777777777", status="converted", linked_phone="919000000000")
        )
        events._handle_comment_event(comment_event("c1"))
        self.assertEqual(self.private_replies, [])
        self.assertEqual(self.public_replies[0][1], templates.COMMENT_REPLY_ON_WHATSAPP_TEXT)

    def test_no_public_reply_is_posted_when_the_dm_could_not_be_delivered(self):
        # The reply's only job is to point at the DM, so it must not claim
        # one arrived when none did.
        with mock.patch.object(events.instagram_messenger, "send_private_reply_to_comment", lambda *a: False):
            events._handle_comment_event(comment_event("c1"))
        self.assertEqual(self.public_replies, [])

    def test_a_comment_with_no_author_id_is_ignored(self):
        event = comment_event("c1")
        event["from"] = {}
        events._handle_comment_event(event)
        self.assertEqual(self.private_replies, [])

    def test_the_daily_comment_allowance_is_enforced_per_account(self):
        limit = events._comment_limits().max_messages
        self.assertGreater(limit, 0, "this test needs a non-zero INSTAGRAM_DAILY_COMMENT_LIMIT")
        for index in range(limit + 3):
            events._handle_comment_event(comment_event(f"c{index}"))
        self.assertEqual(len(self.public_replies), limit)


class SharedReelRules(InstagramWebhookTestCase):
    def test_a_shared_reel_gets_the_two_message_sequence(self):
        events._handle_message_event(
            message_event(
                "m1",
                attachments=[
                    {
                        "type": "ig_reel",
                        "payload": {"reel_video_id": MEDIA_ID, "url": "https://lookaside.fbsbx.com/x"},
                    }
                ],
            )
        )
        # Sharing opens a 24-hour messaging window, so unlike the comment
        # path these are two separate messages: the details, then the link.
        self.assertEqual(len(self.sent_dms), 2)
        self.assertIn("Vesu", self.sent_dms[0][1])
        header, _, link = self.sent_dms[1][1].partition("\n")
        self.assertEqual(header, "Tell us your requirement by clicking on this link:")
        # The link is in this same message -- one non-empty token, not a
        # third message and not blank.
        self.assertTrue(link.strip())
        self.assertNotIn(" ", link)
        self.assertNotIn(link, self.sent_dms[0][1])

    def test_a_share_carrying_the_permalink_needs_no_api_lookup(self):
        events._handle_message_event(
            message_event("m1", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        )
        self.assertEqual(len(self.sent_dms), 2)
        self.assertEqual(self.permalink_lookups, [])

    def test_a_reel_link_pasted_as_text_is_matched_too(self):
        events._handle_message_event(message_event("m1", text=f"is this available? {REEL_URL}"))
        self.assertEqual(len(self.sent_dms), 2)

    def test_an_ordinary_text_message_is_ignored(self):
        events._handle_message_event(message_event("m1", text="hello there"))
        self.assertEqual(self.sent_dms, [])

    def test_our_own_outbound_message_echo_is_ignored(self):
        events._handle_message_event(
            message_event("m1", sender_id=OUR_IG_ID, is_echo=True, attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        )
        self.assertEqual(self.sent_dms, [])

    def test_an_unsent_message_is_ignored(self):
        events._handle_message_event(
            message_event("m1", is_deleted=True, attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        )
        self.assertEqual(self.sent_dms, [])

    def test_a_redelivered_share_sends_nothing_a_second_time(self):
        event = message_event("m1", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        events._handle_message_event(event)
        events._handle_message_event(event)
        self.assertEqual(len(self.sent_dms), 2)

    def test_sharing_again_after_a_comment_still_gets_answered(self):
        # Sharing is its own deliberate action: it must not be swallowed by
        # the comment path's "already sent this person this property" guard.
        events._handle_comment_event(comment_event("c1", from_id="5555555555"))
        events._handle_message_event(
            message_event("m1", sender_id="5555555555", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        )
        self.assertEqual(len(self.sent_dms), 2)

    def test_a_share_is_not_marked_handled_when_sending_failed(self):
        event = message_event("m1", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}])
        with mock.patch.object(events.instagram_messenger, "send_dm_to_user", lambda *a: False):
            events._handle_message_event(event)
        self.assertFalse(instagram_contact_store.is_event_processed("dm_message:m1"))


class PayloadFanOut(InstagramWebhookTestCase):
    """One delivery can carry several entries, and an entry several events."""

    def _run(self, payload: dict) -> None:
        # Run the fan-out inline instead of on the worker pool, so the test
        # asserts on a finished result rather than on timing.
        with mock.patch.object(events, "_pool_handle", lambda: SimpleNamespace(submit=lambda fn, *a: fn(*a))):
            events._process_payload(payload)

    def test_a_batch_of_entries_is_all_handled(self):
        self._run(
            {
                "object": "instagram",
                "entry": [
                    {"id": OUR_IG_ID, "changes": [{"field": "comments", "value": comment_event("c1")}]},
                    {
                        "id": OUR_IG_ID,
                        "messaging": [
                            message_event(
                                "m1", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}]
                            )
                        ],
                    },
                ],
            }
        )
        self.assertEqual(len(self.private_replies), 1)
        self.assertEqual(len(self.sent_dms), 2)

    def test_a_field_value_pair_on_the_entry_itself_is_handled(self):
        # Meta has delivered comment notifications both as entry.changes[]
        # and as a bare field/value on the entry, depending on app type.
        self._run(
            {"object": "instagram", "entry": [{"id": OUR_IG_ID, "field": "comments", "value": comment_event("c1")}]}
        )
        self.assertEqual(len(self.private_replies), 1)

    def test_a_messages_field_change_is_handled(self):
        # THE regression this class exists to prevent. "API setup with
        # Instagram business login" (this app's product) delivers a DM as
        # entry.changes[{"field": "messages", "value": {sender, recipient,
        # message}}] — confirmed against the Meta App Dashboard's own "Send
        # to My Server" sample for the messages field — NOT as
        # entry.messaging[], which is the Messenger-Platform/Facebook-Login-
        # for-Business shape every other test in this file (deliberately)
        # also covers. An earlier version of the dispatcher only recognised
        # field == "comments" here, so every real DM this app has ever
        # received was acknowledged with 200 and silently discarded — this
        # pinned test is what makes that regression impossible to
        # reintroduce without a test failing.
        self._run(
            {
                "object": "instagram",
                "entry": [
                    {
                        "id": OUR_IG_ID,
                        "changes": [
                            {
                                "field": "messages",
                                "value": message_event(
                                    "m1", attachments=[{"type": "ig_reel", "payload": {"url": REEL_URL}}]
                                ),
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(len(self.sent_dms), 2)

    def test_a_messages_field_change_with_no_attachment_is_matched_or_ignored_without_crashing(self):
        # The literal sample the dashboard's test tool sends: plain text,
        # no attachment. Not a share, so nothing should be sent — but it
        # must be REACHED and evaluated, not silently dropped before ever
        # being looked at.
        self._run(
            {
                "object": "instagram",
                "entry": [
                    {
                        "id": OUR_IG_ID,
                        "changes": [
                            {"field": "messages", "value": message_event("random_mid", text="random_text")}
                        ],
                    }
                ],
            }
        )
        self.assertEqual(self.sent_dms, [])
        self.assertTrue(instagram_contact_store.is_event_processed("dm_message:random_mid"))

    def test_an_unknown_field_is_ignored(self):
        self._run(
            {"object": "instagram", "entry": [{"id": OUR_IG_ID, "changes": [{"field": "mentions", "value": {}}]}]}
        )
        self.assertEqual(self.private_replies, [])

    def test_a_malformed_payload_does_not_raise(self):
        for payload in ({}, {"entry": None}, {"entry": ["nonsense"]}, {"entry": [{"changes": "nope"}]}):
            self._run(payload)
        self.assertEqual(self.private_replies, [])


class MessageLengthRules(unittest.TestCase):
    def test_a_short_message_is_sent_whole(self):
        self.assertEqual(messenger.split_for_send("hello"), ["hello"])

    def test_a_long_message_is_split_inside_instagrams_limit(self):
        pieces = messenger.split_for_send("word " * 500)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(messenger.byte_length(piece), messenger.MAX_MESSAGE_BYTES)

    def test_splitting_never_cuts_a_character_in_half(self):
        for piece in messenger.split_for_send("😊" * 600):
            self.assertLessEqual(messenger.byte_length(piece), messenger.MAX_MESSAGE_BYTES)
            piece.encode("utf-8").decode("utf-8")  # raises if a surrogate was split

    def test_an_oversized_combined_reply_shortens_the_details_and_keeps_the_link(self):
        details = "Hi!\n\n" + "\n".join(f"line {i} " + "a" * 60 for i in range(30))
        link_message = "Tell us your requirement by clicking on this link:\nhttps://forms.example/enquire/tok"
        combined = events._combine_for_single_message([details, link_message])
        self.assertLessEqual(messenger.byte_length(combined), messenger.MAX_MESSAGE_BYTES)
        self.assertTrue(combined.endswith(link_message))
        self.assertTrue(combined.startswith("Hi!"))

    def test_a_combined_reply_that_fits_is_left_untouched(self):
        combined = events._combine_for_single_message(["details", "link message"])
        self.assertEqual(combined, "details\n\nlink message")


class WebhookReceiptSummary(unittest.TestCase):
    """The diagnostic line printed the instant a delivery is accepted — see
    instagram_webhook_controller._summarize's own docstring for why it has
    to count the SAME shapes the dispatcher acts on, exactly. A mismatch
    here is what let the messages-field bug read as "0 messaging event(s)"
    instead of pointing straight at the dispatcher."""

    def test_a_comments_change_counts_as_one_comment_event(self):
        self.assertEqual(
            webhook_controller._summarize(
                {"entry": [{"changes": [{"field": "comments", "value": {}}]}]}
            ),
            "1 comment event(s), 0 messaging event(s)",
        )

    def test_a_messages_change_counts_as_one_messaging_event(self):
        # The exact shape confirmed against the Meta App Dashboard's own
        # "Send to My Server" sample for this product.
        self.assertEqual(
            webhook_controller._summarize(
                {"entry": [{"changes": [{"field": "messages", "value": {}}]}]}
            ),
            "0 comment event(s), 1 messaging event(s)",
        )

    def test_an_entry_messaging_array_also_counts(self):
        self.assertEqual(
            webhook_controller._summarize({"entry": [{"messaging": [{}, {}]}]}),
            "0 comment event(s), 2 messaging event(s)",
        )

    def test_a_bare_field_value_pair_on_the_entry_counts_too(self):
        self.assertEqual(
            webhook_controller._summarize({"entry": [{"field": "messages", "value": {}}]}),
            "0 comment event(s), 1 messaging event(s)",
        )

    def test_no_entries_is_reported_plainly(self):
        self.assertEqual(webhook_controller._summarize({}), "no entries")


class SignatureRules(unittest.TestCase):
    """Meta signs every delivery; anyone who knows the URL but not the app
    secret must not be able to make this backend send messages."""

    SECRET = "app-secret"
    BODY = b'{"object":"instagram"}'

    def _with_secrets(self, instagram_secret: str, facebook_secret: str = ""):
        return mock.patch.object(
            webhook_controller,
            "get_settings",
            lambda: SimpleNamespace(
                instagram_app_secret=instagram_secret, facebook_app_secret=facebook_secret
            ),
        )

    def _sign(self, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), self.BODY, hashlib.sha256).hexdigest()

    def test_a_correct_signature_is_accepted(self):
        with self._with_secrets(self.SECRET):
            self.assertTrue(webhook_controller._signature_ok(self.BODY, self._sign(self.SECRET)))

    def test_a_wrong_signature_is_rejected(self):
        with self._with_secrets(self.SECRET):
            self.assertFalse(webhook_controller._signature_ok(self.BODY, self._sign("not-the-secret")))

    def test_a_missing_signature_is_rejected(self):
        with self._with_secrets(self.SECRET):
            self.assertFalse(webhook_controller._signature_ok(self.BODY, None))

    def test_either_configured_secret_may_sign(self):
        # Meta has signed Instagram webhooks with the Facebook app secret on
        # some app types, so both are accepted.
        with self._with_secrets(self.SECRET, "fb-secret"):
            self.assertTrue(webhook_controller._signature_ok(self.BODY, self._sign("fb-secret")))

    def test_a_tampered_body_is_rejected(self):
        with self._with_secrets(self.SECRET):
            self.assertFalse(webhook_controller._signature_ok(b'{"object":"evil"}', self._sign(self.SECRET)))


class ReelCodeRules(unittest.TestCase):
    def test_every_permalink_shape_yields_the_same_code(self):
        for url in (
            f"https://www.instagram.com/reel/{REEL_CODE}/",
            f"https://instagram.com/reels/{REEL_CODE}",
            f"http://www.instagram.com/p/{REEL_CODE}/?igshid=abc",
            f"look at this https://www.instagram.com/reel/{REEL_CODE}/ nice",
        ):
            self.assertEqual(matcher.extract_reel_code(url), REEL_CODE, url)

    def test_a_cdn_link_carries_no_code(self):
        self.assertIsNone(matcher.extract_reel_code("https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1"))
        self.assertIsNone(matcher.extract_reel_code(None))


if __name__ == "__main__":
    unittest.main()
