"""Broker-requirement matching is the SAME engine as client matching.

A broker requirement and a client inquiry are the same thing said by two
different people — "someone wants a 3 BHK in Vesu under 90L" — so the demand
side must contain no scoring logic of its own. These tests hold that line:
each one scores a requirement and the equivalent client inquiry and asserts
they come out identical, so a future change to one surface cannot silently
diverge from the other.

They also cover the two things the demand side genuinely lacked: the shortlist
ceiling, and the ordering used to choose it.

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import List, Optional

from Model.BrokerRequirementModel.broker_requirement import StructuredRequirement
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.BrokerRequirementService import requirement_matching_service as rms
from Service.ClientPropertyMatchingService import match_config as config
from Service.ClientPropertyMatchingService import matching_service, normalization, scoring

CRORE = 10_000_000.0
LAKH = 100_000.0
CLIENT_VECTOR = [1.0, 0.0]
SIMILAR = [0.8, 0.6]


def make_property(**overrides) -> EmbeddedProperty:
    fields = {
        "source_message_id": "msg-1",
        "group_name": "Test Group",
        "chat_type": "group",
        "sender_name": "Sender",
        "sender_saved_name": "Sender",
        "sender_phone": "+919000000000",
        "message_text": "listing",
        "message_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "listing_type": "Sale",
        "embedding": SIMILAR,
        "embedding_model": "test",
    }
    fields.update(overrides)
    return EmbeddedProperty(**fields)


def make_requirement(**overrides) -> StructuredRequirement:
    fields = {
        "source_message_id": "req-msg-1",
        "group_name": "Broker Group",
        "chat_type": "group",
        "sender_name": "Broker",
        "sender_saved_name": "Broker",
        "sender_phone": "+919222222222",
        "message_text": "requirement",
        "message_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "formatted_timestamp": "1 Jan 2026",
        "listing_type": "Sale",
    }
    fields.update(overrides)
    return StructuredRequirement(**fields)


def score_requirement(requirement: StructuredRequirement, prop: EmbeddedProperty) -> Optional[MatchScore]:
    """What the demand side computes for one pair, through its own adapter."""
    pseudo = rms._as_pseudo_client(requirement)
    return scoring.score_client_property(prop, scoring.build_brief(pseudo, CLIENT_VECTOR))


def score_client(client: ClientRecord, prop: EmbeddedProperty) -> Optional[MatchScore]:
    return scoring.score_property(client, prop, CLIENT_VECTOR)


class SameEngineTest(unittest.TestCase):
    """The whole point: identical inputs, identical output."""

    def assert_same(self, requirement: StructuredRequirement, client: ClientRecord, prop: EmbeddedProperty):
        demand = score_requirement(requirement, prop)
        supply = score_client(client, prop)
        if demand is None or supply is None:
            self.assertIsNone(demand)
            self.assertIsNone(supply)
            return
        self.assertEqual(demand.score, supply.score)
        self.assertEqual(demand.bucket, supply.bucket)
        self.assertEqual(demand.confidence_score, supply.confidence_score)
        self.assertEqual(demand.confidence_bucket, supply.confidence_bucket)
        self.assertEqual(demand.field_scores, supply.field_scores)
        self.assertEqual(demand.reason, supply.reason)
        self.assertEqual(demand.matched_requirements, supply.matched_requirements)
        self.assertEqual(demand.missing_information, supply.missing_information)

    def test_a_full_brief_scores_identically_on_both_sides(self):
        self.assert_same(
            make_requirement(requirement_type="Flat", bhk="3 BHK", preferred_areas=["Vesu"],
                             budget_max_inr=1 * CRORE, listing_type="Sale"),
            ClientRecord(phone="+91", purpose="buy", property_type="Flat", bhk="3 BHK",
                         preferred_areas="Vesu", budget_max_inr=1 * CRORE),
            make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu", price_amount_inr=95 * LAKH),
        )

    def test_a_budget_only_brief_scores_identically(self):
        self.assert_same(
            make_requirement(budget_max_inr=1.3 * CRORE),
            ClientRecord(phone="+91", purpose="buy", budget_max_inr=1.3 * CRORE),
            make_property(price_amount_inr=88 * LAKH),
        )

    def test_a_rental_requirement_behaves_like_a_renting_client(self):
        self.assert_same(
            make_requirement(listing_type="Rent", bhk="2 BHK", budget_max_inr=50_000),
            ClientRecord(phone="+91", purpose="rent", bhk="2 BHK", budget_max_inr=50_000),
            make_property(listing_type="Rent", bhk="2 BHK", price_amount_inr=45_000),
        )

    def test_a_missing_price_is_unknown_on_both_sides(self):
        self.assert_same(
            make_requirement(requirement_type="Flat", bhk="3 BHK", preferred_areas=["Vesu"],
                             budget_max_inr=1 * CRORE),
            ClientRecord(phone="+91", purpose="buy", property_type="Flat", bhk="3 BHK",
                         preferred_areas="Vesu", budget_max_inr=1 * CRORE),
            make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu"),
        )

    def test_the_core_requirement_ceiling_applies_to_requirements_too(self):
        """The 4-BHK-shown-a-3-BHK case, on the demand side."""
        match = score_requirement(
            make_requirement(requirement_type="Flat", bhk="4 BHK", preferred_areas=["Green City"],
                             budget_max_inr=90 * LAKH),
            make_property(property_type="Flat", bhk="3 BHK", area_name="Green City",
                          price_amount_inr=60 * LAKH),
        )
        self.assertIsNotNone(match)
        self.assertNotEqual(match.bucket, MatchBucket.HIGH)

    def test_eligibility_rejects_the_same_pairs(self):
        cases = [
            # (requirement kwargs, client kwargs, property kwargs)
            (dict(requirement_type="Flat", bhk="3 BHK"),
             dict(purpose="buy", property_type="Flat", bhk="3 BHK"),
             dict(property_type="Bungalow", bhk="3 BHK")),
            (dict(listing_type="Rent"),
             dict(purpose="rent"),
             dict(listing_type="Sale")),
            (dict(bhk="exactly 3 BHK"),
             dict(purpose="buy", bhk="exactly 3 BHK"),
             dict(bhk="4 BHK")),
            (dict(budget_max_inr=1 * CRORE),
             dict(purpose="buy", budget_max_inr=1 * CRORE),
             dict(price_amount_inr=2 * CRORE)),
        ]
        for requirement_kw, client_kw, property_kw in cases:
            with self.subTest(property_kw=property_kw):
                prop = make_property(**property_kw)
                self.assertIsNone(score_requirement(make_requirement(**requirement_kw), prop))
                self.assertIsNone(score_client(ClientRecord(phone="+91", **client_kw), prop))


class BhkMeansAHomeTest(unittest.TestCase):
    """The rule that used to live only on the demand side. It is now in the
    shared eligibility gate, so BOTH surfaces apply it — which is what fixes
    the client side, where 69 real clients were being shown plots."""

    def test_a_requirement_asking_bhk_with_no_type_skips_land_and_commercial(self):
        requirement = make_requirement(bhk="2 BHK", budget_max_inr=1 * CRORE)
        for non_residential in ("Plot", "Land/Plot", "Shop", "Office", "Warehouse", "Commercial"):
            with self.subTest(property_type=non_residential):
                self.assertIsNone(
                    score_requirement(requirement, make_property(property_type=non_residential,
                                                                 bhk="2 BHK", price_amount_inr=80 * LAKH))
                )

    def test_a_client_asking_bhk_with_no_type_now_skips_them_too(self):
        client = ClientRecord(phone="+91", bhk="2 BHK", budget_max_inr=1 * CRORE)
        for non_residential in ("Plot", "Shop"):
            with self.subTest(property_type=non_residential):
                self.assertIsNone(
                    score_client(client, make_property(property_type=non_residential, bhk="2 BHK",
                                                       price_amount_inr=80 * LAKH))
                )

    def test_homes_are_unaffected(self):
        client = ClientRecord(phone="+91", bhk="2 BHK", budget_max_inr=1 * CRORE)
        for residential in ("Flat", "Apartment", "Bungalow", "Row House"):
            with self.subTest(property_type=residential):
                self.assertIsNotNone(
                    score_client(client, make_property(property_type=residential, bhk="2 BHK",
                                                       price_amount_inr=80 * LAKH))
                )

    def test_an_unknown_type_is_still_scored(self):
        """Only a type that positively says land or commercial is excluded —
        nothing is assumed about a listing that does not say."""
        client = ClientRecord(phone="+91", bhk="2 BHK", budget_max_inr=1 * CRORE)
        self.assertIsNotNone(score_client(client, make_property(bhk="2 BHK", price_amount_inr=80 * LAKH)))

    def test_naming_a_type_lets_land_through_again(self):
        """Someone who asks for a Plot AND writes a BHK gets plots: they said
        what they want, and the rule only ever fills in a MISSING type."""
        client = ClientRecord(phone="+91", property_type="Plot", bhk="2 BHK", budget_max_inr=1 * CRORE)
        self.assertIsNotNone(
            score_client(client, make_property(property_type="Plot", price_amount_inr=80 * LAKH))
        )

    def test_the_rule_is_configurable(self):
        from unittest import mock

        client = ClientRecord(phone="+91", bhk="2 BHK", budget_max_inr=1 * CRORE)
        with mock.patch.object(config, "REJECT_NON_RESIDENTIAL_FOR_BHK", False):
            self.assertIsNotNone(
                score_client(client, make_property(property_type="Plot", bhk="2 BHK",
                                                   price_amount_inr=80 * LAKH))
            )

    def test_the_two_surfaces_agree_exactly(self):
        prop = make_property(property_type="Plot", bhk="2 BHK", price_amount_inr=80 * LAKH)
        self.assertIsNone(score_requirement(make_requirement(bhk="2 BHK", budget_max_inr=1 * CRORE), prop))
        self.assertIsNone(score_client(ClientRecord(phone="+91", bhk="2 BHK", budget_max_inr=1 * CRORE), prop))


class RequirementCeilingTest(unittest.TestCase):
    """§18's ceiling, which the demand side did not have. Without it a
    requirement naming only a budget matches most of the property list, and the
    nightly catch-up covers a thousand requirements — so the demand side alone
    could have grown past a million rows in Neon."""

    def matches(self, count: int) -> List[MatchScore]:
        return [
            MatchScore(
                record_id=f"p{index}",
                score=round(0.99 - index * 0.0005, 4),
                bucket=MatchBucket.HIGH,
                confidence_score=0.5,
                evidence_ratio=1.0,
                is_partial_match=False,
                property_category="main",
                field_scores={"budget": 1.0},
                reason="",
            )
            for index in range(count)
        ]

    def test_the_ceiling_exists_and_equals_the_client_ceiling(self):
        self.assertEqual(config.MAX_MATCHES_PER_REQUIREMENT, config.MAX_MATCHES_PER_CLIENT)
        self.assertEqual(config.MAX_MATCHES_PER_REQUIREMENT, 100)

    def test_only_the_highest_ranked_are_kept(self):
        kept = rms.best_matches(self.matches(400))
        self.assertEqual(len(kept), config.MAX_MATCHES_PER_REQUIREMENT)
        self.assertEqual({m.record_id for m in kept}, {f"p{i}" for i in range(100)})

    def test_a_shorter_list_is_returned_whole(self):
        self.assertEqual(len(rms.best_matches(self.matches(12))), 12)

    def test_it_ranks_the_same_way_the_client_side_does(self):
        listing = self.matches(250)
        self.assertEqual(
            {m.record_id for m in rms.best_matches(listing)},
            {m.record_id for m in matching_service._best_matches(list(listing))},
        )

    def test_confidence_breaks_a_tie_here_too(self):
        low = self.matches(1)[0]
        high = low.model_copy(update={"record_id": "better", "confidence_score": 0.95})
        kept = rms.best_matches([low, high] + self.matches(150)[50:])
        self.assertIn("better", {m.record_id for m in kept})


class NoScoringLogicOfItsOwnTest(unittest.TestCase):
    """A structural guard. The demand side's job is to ADAPT a requirement into
    the shape the engine reads (rms._as_pseudo_client) and nothing else; the
    moment it grows a threshold or a weight of its own, the two surfaces have
    begun to drift and this test is where that should be noticed."""

    def test_the_adapter_maps_every_field_the_engine_reads(self):
        requirement = make_requirement(
            requirement_type="Flat, Row House", bhk="3 BHK", preferred_areas=["Vesu", "Piplod"],
            society_name="Black Residency", furnishing=normalization.SEMI_FURNISHED,
            budget_min_inr=80 * LAKH, budget_max_inr=1 * CRORE, listing_type="Rent",
            description="veg family only",
        )
        pseudo = rms._as_pseudo_client(requirement)
        self.assertEqual(pseudo.purpose, "rent")
        self.assertEqual(pseudo.property_type, "Flat, Row House")
        self.assertEqual(pseudo.bhk, "3 BHK")
        self.assertEqual(pseudo.preferred_areas, "Vesu, Piplod")
        self.assertEqual(pseudo.furnishing, normalization.SEMI_FURNISHED)
        self.assertEqual(pseudo.budget_min_inr, 80 * LAKH)
        self.assertEqual(pseudo.budget_max_inr, 1 * CRORE)
        self.assertIn("Black Residency", pseudo.additional_requirements)
        self.assertIn("veg family only", pseudo.additional_requirements)

    def test_a_sale_requirement_becomes_a_buying_client(self):
        self.assertEqual(rms._as_pseudo_client(make_requirement(listing_type="Sale")).purpose, "buy")

    def test_area_name_is_only_a_fallback_for_an_empty_list(self):
        self.assertEqual(rms._as_pseudo_client(make_requirement(area_name="Vesu")).preferred_areas, "Vesu")
        self.assertEqual(
            rms._as_pseudo_client(make_requirement(area_name="Vesu", preferred_areas=["Piplod"])).preferred_areas,
            "Piplod",
        )

    def test_the_demand_side_defines_no_thresholds_of_its_own(self):
        forbidden = ("CUTOFF", "WEIGHT", "FLOOR", "TOLERANCE", "CEILING", "BAND")
        offenders = [
            name
            for name in dir(rms)
            if name.isupper() and any(word in name for word in forbidden)
        ]
        self.assertEqual(offenders, [], f"scoring constants leaked into the demand side: {offenders}")

    def test_multi_type_requirements_are_tagged_like_multi_type_clients(self):
        requirement = make_requirement(requirement_type="Flat, Row House", budget_max_inr=1 * CRORE)
        match = score_requirement(requirement, make_property(property_type="Row House",
                                                             price_amount_inr=95 * LAKH))
        self.assertIsNotNone(match)
        self.assertEqual(match.matched_type, "Row House")


class EmptyRequirementTest(unittest.TestCase):
    """A requirement that asks for NOTHING must match nothing.

    It used to match everything: _as_pseudo_client always fills `purpose`
    (listing_type is a Literal defaulting to "Sale"), so the shared
    has_requirements check could never answer False on this side, and a
    blank requirement was scored against every stored property on semantic
    similarity alone."""

    def test_a_blank_requirement_has_nothing_to_match_on(self):
        self.assertFalse(rms._has_criteria(rms._as_pseudo_client(make_requirement())))

    def test_a_blank_rent_requirement_has_nothing_to_match_on_either(self):
        self.assertFalse(rms._has_criteria(rms._as_pseudo_client(make_requirement(listing_type="Rent"))))

    def test_anything_actually_stated_still_counts(self):
        for field, value in (
            ("requirement_type", "Flat"),
            ("bhk", "3 BHK"),
            ("budget_min_inr", 50 * LAKH),
            ("budget_max_inr", 50 * LAKH),
            ("preferred_areas", ["Vesu"]),
            ("area_name", "Vesu"),
            ("furnishing", "Fully furnished"),
            ("society_name", "Black Residency"),
            ("description", "veg family, possession Sep"),
        ):
            with self.subTest(field=field):
                requirement = make_requirement(**{field: value})
                self.assertTrue(rms._has_criteria(rms._as_pseudo_client(requirement)))

    def test_the_client_side_is_untouched(self):
        """A CLIENT really can state only a purpose — that is a real brief
        there, and this fix must not have reached it."""
        self.assertTrue(matching_service.has_requirements(ClientRecord(phone="+919000000001", purpose="buy")))


if __name__ == "__main__":
    unittest.main()
