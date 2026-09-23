"""What an EDITED listing may and may not do to a client's stored matches.

The rule these tests pin down, in the words it was asked for:

    Property A is in c1's, c2's and c3's matches. For c1 it is only a match;
    for c2 an agent is already assigned to it; for c3 a visit to it has been
    completed. A is then edited — 3 BHK becomes 4 BHK.

    It must leave c1's matches (it is neither assigned nor completed there),
    and it must STAY, untouched, for c2 and c3 — same bucket, same score,
    showing the listing's new details.

    The next morning's 6 AM pass then reconsiders A for c1 alone: if it still
    matches the edited details it comes back, and if it does not it stays
    away. For c2 and c3 the pass must not look at it at all, even though it
    was edited after those two arrived.

The first half (what the edit itself does) is
Service/ClientPropertyMatchingService/match_invalidation_service.py plus
Database/edited_property_match_repository.py; the second half (what the
nightly pass does afterwards) is
matching_service.rescore_changed_properties' PROTECTED PAIRS.

Everything here runs against the in-memory backend, never a database — see
test_instagram_webhooks.py's own setUp for why that matters.

Run from the Backend directory:

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import List, Optional
from unittest import mock

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import match_invalidation_service, matching_service

CRORE = 10_000_000.0

# Unit-normalized stand-ins for "the descriptions are similar", exactly as
# test_matching_engine.py uses — no model is run.
CLIENT_VECTOR = [1.0, 0.0]
SIMILAR = [0.8, 0.6]


def make_property(
    record_id: str,
    bhk: Optional[str] = "3 BHK",
    price: float = 1.0 * CRORE,
    area: str = "Vesu",
) -> EmbeddedProperty:
    return EmbeddedProperty(
        record_id=record_id,
        source_message_id=f"msg-{record_id}",
        property_type="Flat",
        bhk=bhk,
        area_name=area,
        price_amount_inr=price,
        price_text=f"{price / CRORE:.2f} cr",
        listing_type="Sale",
        group_name="g",
        chat_type="group",
        sender_name="s",
        sender_saved_name="s",
        sender_phone="+919000000000",
        message_text="t",
        message_timestamp=datetime.now(timezone.utc),
        embedding=SIMILAR,
        embedding_model="test",
    )


def make_client(phone: str, bhk: str = "3 BHK") -> ClientRecord:
    return ClientRecord(
        phone=phone,
        purpose="buy",
        property_type="Flat",
        bhk=bhk,
        budget_min_inr=0.9 * CRORE,
        budget_max_inr=1.1 * CRORE,
        preferred_areas="Vesu",
    )


class ProtectedPairsTest(unittest.TestCase):
    """The nightly incremental pass, asked about an edited listing."""

    def setUp(self) -> None:
        # In-memory backend for the whole test, pinned before anything reads
        # it. matching_service asks this question on every persist and every
        # read, so patching it here covers the lot.
        db_off = mock.patch.object(matching_service, "is_client_database_configured", lambda: False)
        db_off.start()
        self.addCleanup(db_off.stop)
        matching_service._score_cache.clear()
        matching_service._computed_at_cache.clear()

    def _seed(self, phone: str, prop: EmbeddedProperty) -> None:
        """Gives this client one cached match against `prop`, the way a full
        recompute would."""
        client = make_client(phone)
        matching_service.recompute_for_client_record(
            client, CLIENT_VECTOR, candidates=[prop], stamp_watermark=False
        )
        self.assertEqual(
            [score.record_id for score in matching_service._score_cache[phone]],
            [prop.record_id],
            "seeding failed — the fixture no longer matches at all",
        )

    def _rescore(self, phone: str, changed: List[EmbeddedProperty], protected: List[str]) -> None:
        matching_service.rescore_changed_properties(
            make_client(phone),
            changed,
            datetime.now(timezone.utc),
            stored_vector=CLIENT_VECTOR,
            protected_record_ids=protected,
        )

    def test_unprotected_client_loses_a_listing_that_no_longer_matches(self) -> None:
        """c1: only a match, so the edited listing is reconsidered and goes.

        The edit used here is a price well past this client's ceiling, not
        the story's 3 BHK -> 4 BHK: the engine treats one bedroom out as a
        PARTIAL match rather than a disqualification (see scoring.is_eligible
        and match_config), so a 4 BHK still earns a place on a 3 BHK brief.
        What is being pinned here is the rule — an unprotected listing is
        re-examined and kept only if it still matches — not which particular
        edit crosses the line."""
        self._seed("+919111111111", make_property("A"))
        self._rescore("+919111111111", [make_property("A", price=9.0 * CRORE)], protected=[])
        self.assertEqual(matching_service._score_cache["+919111111111"], [])

    def test_unprotected_client_keeps_a_listing_that_still_matches(self) -> None:
        """The other half of the same rule: re-scored, and it earns its place
        again — the edit is not a removal, it is a re-examination."""
        self._seed("+919111111111", make_property("A"))
        self._rescore("+919111111111", [make_property("A", price=1.02 * CRORE)], protected=["B"])
        self.assertEqual(
            [score.record_id for score in matching_service._score_cache["+919111111111"]], ["A"]
        )

    def test_assigned_client_keeps_the_listing_untouched(self) -> None:
        """c2: an agent is already out to A, so the pass must not look at it
        — not to drop it, and not to move it."""
        phone = "+919222222222"
        self._seed(phone, make_property("A"))
        before = matching_service._score_cache[phone][0]
        self._rescore(phone, [make_property("A", bhk="4 BHK")], protected=["A"])
        after = matching_service._score_cache[phone]
        self.assertEqual([score.record_id for score in after], ["A"])
        self.assertEqual(after[0].score, before.score, "the score moved for a protected pair")
        self.assertEqual(after[0].bucket, before.bucket, "the bucket moved for a protected pair")

    def test_completed_client_keeps_the_listing_untouched(self) -> None:
        """c3: identical rule, reached through a completed visit instead of
        an active assignment — the caller passes one set for both."""
        phone = "+919333333333"
        self._seed(phone, make_property("A"))
        before = matching_service._score_cache[phone][0]
        self._rescore(phone, [make_property("A", area="Adajan", bhk="4 BHK")], protected=["A"])
        after = matching_service._score_cache[phone]
        self.assertEqual([score.record_id for score in after], ["A"])
        self.assertEqual(after[0].score, before.score)
        self.assertEqual(after[0].bucket, before.bucket)

    def test_a_needs_review_listing_is_still_kept_when_protected(self) -> None:
        """A protected listing pushed into the review queue is not matchable
        any more, which for anyone else means its cached row goes. For a
        client already assigned to it, it is history and stays."""
        phone = "+919444444444"
        self._seed(phone, make_property("A"))
        flagged = make_property("A")
        flagged.needs_review = True
        self._rescore(phone, [flagged], protected=["A"])
        self.assertEqual([score.record_id for score in matching_service._score_cache[phone]], ["A"])

    def test_a_protected_listing_never_blocks_the_rest_of_the_night(self) -> None:
        """Only the protected listing is skipped — everything else in the
        same run is scored exactly as before."""
        phone = "+919555555555"
        self._seed(phone, make_property("A"))
        self._rescore(
            phone,
            [make_property("A", bhk="4 BHK"), make_property("B")],
            protected=["A"],
        )
        self.assertEqual(
            sorted(score.record_id for score in matching_service._score_cache[phone]), ["A", "B"]
        )

    def test_nothing_is_written_when_every_changed_listing_is_protected(self) -> None:
        """The cheapest outcome: no score, no merge, no computed_at stamp."""
        phone = "+919666666666"
        self._seed(phone, make_property("A"))
        stamp = matching_service._computed_at_cache[phone]
        written = matching_service.rescore_changed_properties(
            make_client(phone),
            [make_property("A", bhk="4 BHK")],
            datetime.now(timezone.utc),
            stored_vector=CLIENT_VECTOR,
            protected_record_ids=["A"],
        )
        self.assertEqual(written, 0)
        self.assertEqual(matching_service._computed_at_cache[phone], stamp)

    def test_the_ceiling_never_evicts_a_protected_listing(self) -> None:
        """A client's shortlist is capped, and a night of new arrivals must
        not be able to push their assigned property out of it."""
        phone = "+919777777777"
        # One low-scoring protected listing, plus a full shortlist of
        # stronger ones arriving tonight.
        weak = make_property("A", price=1.09 * CRORE)
        self._seed(phone, weak)
        arrivals = [make_property(f"N{index}") for index in range(matching_service.MAX_MATCHES_PER_CLIENT)]
        self._rescore(phone, arrivals, protected=["A"])
        kept = {score.record_id for score in matching_service._score_cache[phone]}
        self.assertIn("A", kept, "the ceiling evicted a protected listing")


class NeutralEditTest(unittest.TestCase):
    """Which edits are even capable of moving a score — the question asked
    before any of the above runs at all."""

    def test_display_only_edits_cost_a_listing_nothing(self) -> None:
        for field, value in (
            ("image_urls", ["https://example.com/1.jpg"]),
            ("instagram_reel_url", "https://instagram.com/reel/x"),
            ("location_url", "https://maps.example/x"),
            ("video_available", True),
            ("unit_no", "B-1204"),
            ("contact_phones", ["+919000000001"]),
            ("extra_notes", "owner prefers evening calls"),
        ):
            with self.subTest(field=field):
                self.assertFalse(match_invalidation_service.edit_affects_matching({field: value}))

    def test_everything_scoring_reads_does_cost_it(self) -> None:
        for field, value in (
            ("bhk", "4 BHK"),
            ("price_amount_inr", 2.0 * CRORE),
            ("area_name", "Adajan"),
            ("property_type", "Bungalow"),
            ("furnishing", "Full furnished"),
            # The free text that IS embedded — the whole semantic half of the
            # score is built on it, so it can never be neutral.
            ("description", "now with a private terrace"),
        ):
            with self.subTest(field=field):
                self.assertTrue(match_invalidation_service.edit_affects_matching({field: value}))

    def test_a_listing_pushed_into_the_review_queue_always_counts(self) -> None:
        self.assertTrue(match_invalidation_service.edit_affects_matching(None, needs_review=True))

    def test_only_values_that_actually_moved_are_judged(self) -> None:
        """The Edit dialog posts the whole form every time, so the neutral
        list only means anything once the unchanged fields are removed."""
        existing = make_property("A")
        moved = match_invalidation_service.changed_fields(
            existing, {"bhk": "3 BHK", "area_name": "Vesu", "extra_notes": "noted"}
        )
        self.assertEqual(moved, {"extra_notes": "noted"})
        self.assertFalse(match_invalidation_service.edit_affects_matching(moved))


class AvailabilityTest(unittest.TestCase):
    """The "AVL or Not" toggle has NO relationship with matching.

    It is the client's own off-the-market-for-now flag. An unavailable
    listing is still scored, still ranked, still kept in every shortlist and
    still assignable; flipping the toggle costs it nothing; and the only
    thing that happens anywhere is that the matches dialogs mark the card.
    Distinct from the Sold out tab, which really does remove a listing and
    its match rows.
    """

    def setUp(self) -> None:
        db_off = mock.patch.object(matching_service, "is_client_database_configured", lambda: False)
        db_off.start()
        self.addCleanup(db_off.stop)
        matching_service._score_cache.clear()
        matching_service._computed_at_cache.clear()

    def test_flipping_the_toggle_alone_is_a_neutral_edit(self) -> None:
        """No purge, so the listing keeps its place in every client's and
        every requirement's stored matches."""
        self.assertFalse(match_invalidation_service.edit_affects_matching({"is_available": False}))
        self.assertFalse(match_invalidation_service.edit_affects_matching({"is_available": True}))

    def test_it_is_still_neutral_beside_a_photo(self) -> None:
        self.assertFalse(
            match_invalidation_service.edit_affects_matching(
                {"is_available": False, "image_urls": ["x"], "extra_notes": "n"}
            )
        )

    def test_a_real_edit_in_the_same_save_still_counts(self) -> None:
        """The toggle being neutral must never make a price change neutral
        by travelling with it."""
        self.assertTrue(
            match_invalidation_service.edit_affects_matching(
                {"is_available": False, "price_amount_inr": 2.0 * CRORE}
            )
        )

    def test_an_unavailable_listing_still_scores_identically(self) -> None:
        """Availability reaches neither the eligibility gate nor any field
        score — the two listings below differ only by the toggle."""
        available = make_property("A")
        off_market = make_property("A")
        off_market.is_available = False
        from Service.ClientPropertyMatchingService import scoring

        brief = scoring.build_brief(make_client("+919111111111"), CLIENT_VECTOR)
        first = scoring.score_client_property(available, brief)
        second = scoring.score_client_property(off_market, brief)
        self.assertIsNotNone(second)
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.bucket, second.bucket)

    def test_an_unavailable_listing_is_still_matchable(self) -> None:
        off_market = make_property("A")
        off_market.is_available = False
        self.assertTrue(matching_service.is_matchable(off_market))

    def test_an_unavailable_listing_keeps_its_place_in_the_shortlist(self) -> None:
        """The end-to-end statement: it is cached, it survives a nightly
        rescore, and it comes back in the result."""
        phone = "+919999999999"
        off_market = make_property("A")
        off_market.is_available = False
        client = make_client(phone)
        matching_service.recompute_for_client_record(
            client, CLIENT_VECTOR, candidates=[off_market], stamp_watermark=False
        )
        self.assertEqual([score.record_id for score in matching_service._score_cache[phone]], ["A"])
        matching_service.rescore_changed_properties(
            client, [off_market], datetime.now(timezone.utc), stored_vector=CLIENT_VECTOR
        )
        self.assertEqual([score.record_id for score in matching_service._score_cache[phone]], ["A"])

    def test_the_result_carries_the_live_flag_to_the_card(self) -> None:
        """What the dialogs mark on: read from the live listing at read time,
        never from the stored score row."""
        off_market = make_property("A")
        off_market.is_available = False
        self.assertFalse(matching_service.display_fields(off_market)["is_available"])
        self.assertTrue(matching_service.display_fields(make_property("B"))["is_available"])

    def test_a_match_row_never_stores_availability(self) -> None:
        """It is a display field, so nothing about it is written to the
        hundreds of thousands of cached match rows."""
        from Database.matching_repository import _SCORE_COLUMNS, _row_values
        from Model.ClientPropertyMatchingModel.match_score import MatchScore

        self.assertNotIn("is_available", _SCORE_COLUMNS)
        row = _row_values(
            "+919111111111",
            MatchScore(
                record_id="A", score=0.9, bucket=MatchBucket.HIGH, confidence_score=0.9,
                evidence_ratio=1.0, is_partial_match=False, property_category="main",
                field_scores={}, reason="", matched_type=None,
            ),
        )
        self.assertNotIn("is_available", row)


class RequirementNeutralEditTest(unittest.TestCase):
    """The demand side's own version of the same question."""

    def test_staff_only_fields_never_re_score_a_requirement(self) -> None:
        from Service.BrokerRequirementService import requirement_pipeline_service

        neutral = requirement_pipeline_service.MATCH_NEUTRAL_REQUIREMENT_FIELDS
        for field in ("notes", "contact_name", "contact_phones"):
            self.assertIn(field, neutral)
        # The free text a requirement states its extra wishes in is embedded
        # and scored (requirement_matching_service._as_pseudo_client folds it
        # into additional_requirements), so it must never be listed neutral.
        self.assertNotIn("description", neutral)


class ClientNeutralEditTest(unittest.TestCase):
    """And the client side's."""

    def test_staff_notes_and_photos_do_not_re_run_matching(self) -> None:
        base = make_client("+919888888888")
        for field, value in (
            ("name", "Renamed"),
            ("email", "x@example.com"),
            ("has_photo", True),
            ("current_address", "somewhere"),
            ("about_loan", "pre-approved"),
            ("notes", "called twice"),
            ("follow_up_report", "said maybe"),
            ("additional_phones", ["+919000000002"]),
        ):
            with self.subTest(field=field):
                changed = base.model_copy(update={field: value})
                self.assertFalse(matching_service.requirement_fields_changed(base, changed))

    def test_the_brief_itself_does(self) -> None:
        base = make_client("+919888888888")
        for field, value in (
            ("bhk", "4 BHK"),
            ("budget_max_inr", 2.0 * CRORE),
            ("preferred_areas", "Adajan"),
            ("property_type", "Bungalow"),
            ("furnishing", "Full furnished"),
            # "Anything else" on the inquiry form — embedded and scored.
            ("additional_requirements", "needs a temple nearby"),
        ):
            with self.subTest(field=field):
                changed = base.model_copy(update={field: value})
                self.assertTrue(matching_service.requirement_fields_changed(base, changed))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
