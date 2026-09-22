"""The Client-Property matching engine's behaviour, stated as tests.

These are the RULES, not the arithmetic: each one says something a broker
would recognise ("someone who asked to rent is never shown something for
sale", "a listing with no price is not a perfect budget match"). The weights
and cutoffs in match_config.py are meant to be calibrated later, so a test
that pinned an exact score would have to be rewritten every time one moved
and would stop protecting anything. Where a number IS asserted exactly, it is
because the number is the point (a worked example from the specification).

Run from the Backend directory:

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import List, Optional
from unittest import mock

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import match_config as config
from Service.ClientPropertyMatchingService import matching_service, normalization, scoring

CRORE = 10_000_000.0
LAKH = 100_000.0

# A client vector and a property vector whose dot product is a fixed 0.8 —
# the embeddings both sides carry are unit-normalized, so this stands in for
# "the descriptions are fairly similar" without running the model.
CLIENT_VECTOR = [1.0, 0.0]
SIMILAR = [0.8, 0.6]
IDENTICAL = [1.0, 0.0]
UNRELATED = [0.0, 1.0]


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


def make_client(**overrides) -> ClientRecord:
    return ClientRecord(phone="+919111111111", **overrides)


def score(
    client: ClientRecord, prop: EmbeddedProperty, vector: Optional[List[float]] = None
) -> Optional[MatchScore]:
    return scoring.score_property(client, prop, CLIENT_VECTOR if vector is None else vector)


class EligibilityPurposeTest(unittest.TestCase):
    """§2 — purpose is a PASS/FAIL rule, and only when the client stated one."""

    def test_buyer_is_never_shown_a_rental(self):
        client = make_client(purpose="buy", budget_max_inr=1 * CRORE)
        rental = make_property(listing_type="Rent", price_amount_inr=90 * LAKH)
        self.assertIsNone(score(client, rental))

    def test_renter_is_never_shown_a_sale(self):
        client = make_client(purpose="rent", budget_max_inr=50_000)
        sale = make_property(listing_type="Sale", price_amount_inr=45_000)
        self.assertIsNone(score(client, sale))

    def test_matching_purpose_passes_and_is_explained(self):
        client = make_client(purpose="buy", budget_max_inr=1 * CRORE)
        result = score(client, make_property(listing_type="Sale", price_amount_inr=90 * LAKH))
        self.assertIsNotNone(result)
        self.assertIn("purpose", result.field_scores)
        self.assertIn("For sale, as requested", result.reasons)

    def test_unstated_purpose_filters_nothing(self):
        """A client who never said buy or rent has ruled nothing out."""
        client = make_client(budget_max_inr=1 * CRORE)
        for listing_type in ("Sale", "Rent"):
            with self.subTest(listing_type=listing_type):
                result = score(client, make_property(listing_type=listing_type, price_amount_inr=90 * LAKH))
                self.assertIsNotNone(result)
                # ...and purpose is not scored at all, so it cannot dilute
                # anything either.
                self.assertNotIn("purpose", result.field_scores)


class EligibilityPropertyTypeTest(unittest.TestCase):
    """§2, §7, and worked examples B and F."""

    def test_example_b_bungalow_is_rejected_for_a_flat_buyer(self):
        """Price and location must not compensate for the wrong kind of
        property. The bungalow here is cheaper AND in the right city."""
        client = make_client(
            purpose="buy", property_type="Flat", bhk="3 BHK",
            preferred_areas="Ahmedabad", budget_max_inr=1.5 * CRORE,
        )
        bungalow = make_property(
            property_type="Bungalow", bhk="3 BHK", area_name="Ahmedabad",
            price_amount_inr=1.2 * CRORE,
        )
        self.assertIsNone(score(client, bungalow))

    def test_example_f_apartment_is_compatible_with_flat(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK")
        result = score(client, make_property(property_type="Apartment", bhk="3 BHK"))
        self.assertIsNotNone(result)
        self.assertEqual(result.field_scores["property_type"], 1.0)
        self.assertEqual(result.field_scores["bhk"], 1.0)
        self.assertIn("property_type", result.matched_requirements)
        self.assertIn("bhk", result.matched_requirements)

    def test_penthouse_is_compatible_with_flat(self):
        client = make_client(property_type="Flat")
        self.assertIsNotNone(score(client, make_property(property_type="Penthouse")))

    def test_plot_and_shop_are_rejected_for_a_flat_buyer(self):
        client = make_client(property_type="Flat")
        for wrong in ("Plot", "Land/Plot", "Shop", "Office", "Warehouse"):
            with self.subTest(property_type=wrong):
                self.assertIsNone(score(client, make_property(property_type=wrong)))

    def test_villa_is_compatible_with_row_house(self):
        client = make_client(property_type="Row House")
        for sibling in ("Villa", "Bungalow", "Duplex", "Farmhouse"):
            with self.subTest(property_type=sibling):
                self.assertIsNotNone(score(client, make_property(property_type=sibling)))

    def test_unstated_type_filters_nothing(self):
        """§2's "never assume a missing client field means a particular type"
        — and never assume it means all of them either: nothing is filtered,
        and property type is not scored."""
        client = make_client(budget_max_inr=1 * CRORE)
        for any_type in ("Flat", "Bungalow", "Plot", "Shop"):
            with self.subTest(property_type=any_type):
                result = score(client, make_property(property_type=any_type, price_amount_inr=90 * LAKH))
                self.assertIsNotNone(result)
                self.assertNotIn("property_type", result.field_scores)

    def test_one_incompatible_type_among_several_does_not_score_the_property(self):
        """A client who picked Flat AND Plot sees a flat under "Flat" — never
        tagged as, or scored against, the Plot they also picked."""
        client = make_client(property_type="Flat, Plot")
        result = score(client, make_property(property_type="Flat"))
        self.assertIsNotNone(result)
        self.assertEqual(result.matched_type, "Flat")


class EligibilityBhkTest(unittest.TestCase):
    """§2, §6, and worked example E."""

    def test_exact_bhk_scores_full(self):
        client = make_client(bhk="3 BHK")
        result = score(client, make_property(bhk="3 BHK"))
        self.assertEqual(result.field_scores["bhk"], 1.0)
        self.assertIn("Exactly the requested BHK", result.reasons)

    def test_neighbouring_bhk_stays_eligible_but_scores_lower(self):
        client = make_client(bhk="3 BHK")
        for neighbour in ("2 BHK", "4 BHK"):
            with self.subTest(bhk=neighbour):
                result = score(client, make_property(bhk=neighbour))
                self.assertIsNotNone(result)
                self.assertLess(result.field_scores["bhk"], 1.0)

    def test_unacceptable_bhk_is_rejected(self):
        client = make_client(bhk="3 BHK")
        for far in ("1 BHK", "5 BHK", "6 BHK"):
            with self.subTest(bhk=far):
                self.assertIsNone(score(client, make_property(bhk=far)))

    def test_example_e_exactly_three_bhk_rejects_a_four_bhk(self):
        client = make_client(property_type="Flat", bhk="exactly 3 BHK")
        self.assertIsNone(score(client, make_property(property_type="Flat", bhk="4 BHK")))
        self.assertIsNotNone(score(client, make_property(property_type="Flat", bhk="3 BHK")))

    def test_budget_and_location_cannot_rescue_an_unacceptable_bhk(self):
        """§6's explicit rule, checked against the most tempting case: a
        perfect price in exactly the right area."""
        client = make_client(
            bhk="3 BHK", preferred_areas="Vesu", budget_min_inr=80 * LAKH, budget_max_inr=1 * CRORE
        )
        perfect_but_wrong_size = make_property(
            bhk="1 BHK", area_name="Vesu", price_amount_inr=90 * LAKH, embedding=IDENTICAL
        )
        self.assertIsNone(score(client, perfect_but_wrong_size))

    def test_a_range_accepts_everything_inside_it(self):
        client = make_client(bhk="2 to 4 BHK")
        for inside in ("2 BHK", "3 BHK", "4 BHK"):
            with self.subTest(bhk=inside):
                self.assertEqual(score(client, make_property(bhk=inside)).field_scores["bhk"], 1.0)

    def test_unstated_bhk_filters_nothing(self):
        client = make_client(budget_max_inr=1 * CRORE)
        result = score(client, make_property(bhk="1 BHK", price_amount_inr=90 * LAKH))
        self.assertIsNotNone(result)
        self.assertNotIn("bhk", result.field_scores)

    def test_bhk_distance_is_measured_in_bedrooms(self):
        self.assertEqual(normalization.bhk_distance("3 BHK", "3 BHK"), 0.0)
        self.assertEqual(normalization.bhk_distance("3 BHK", "4 BHK"), 1.0)
        self.assertEqual(normalization.bhk_distance("3 BHK", "1 BHK"), 2.0)
        self.assertEqual(normalization.bhk_distance("minimum 3 BHK", "5 BHK"), 0.0)
        self.assertIsNone(normalization.bhk_distance("3 BHK", None))
        self.assertIsNone(normalization.bhk_distance(None, "3 BHK"))


class EligibilityBudgetTest(unittest.TestCase):
    """§2's budget hard filter — TWO-sided now, narrow, and configurable.

    It used to reject only the too-expensive, on the reasoning that a cheaper
    property is not a worse one. With 1,600 listings that reasoning stopped
    holding: a ₹1cr brief kept everything from nothing up to ₹1.25cr, which
    is most of the database, and budget is the heaviest field there is — so
    almost every brief filled its hundred-row shortlist with properties
    nobody would ever call about. Both edges are now a ratio of the client's
    own target band, so they mean the same thing at every price."""

    def setUp(self):
        self.client = make_client(budget_max_inr=1 * CRORE)

    def test_within_budget_is_eligible(self):
        self.assertIsNotNone(score(self.client, make_property(price_amount_inr=95 * LAKH)))

    def test_slightly_over_budget_stays_eligible(self):
        """Somebody who says "up to ₹1cr" will stretch a little for the right
        property. A tenth is a little; a quarter, which is what this used to
        allow, is a different budget."""
        self.assertIsNotNone(score(self.client, make_property(price_amount_inr=1.08 * CRORE)))

    def test_at_the_tolerance_boundary_is_still_eligible(self):
        """§2's boundary case, asserted where it belongs — on the eligibility
        gate. Whether such a property is then worth SHOWING is a separate
        question the match score answers (and for a client whose only stated
        requirement is the budget, a price over it is most of what there was
        to judge on, so it can rank below the display floor). Eligibility and
        worth-showing are deliberately two decisions."""
        brief = scoring.build_brief(self.client, CLIENT_VECTOR)
        at_boundary = 1 * CRORE * (1 + config.BUDGET_OVER_TOLERANCE)
        self.assertTrue(scoring.is_eligible(make_property(price_amount_inr=at_boundary), brief))

    def test_far_over_budget_is_rejected_outright(self):
        brief = scoring.build_brief(self.client, CLIENT_VECTOR)
        self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=1.40 * CRORE), brief))
        self.assertIsNone(score(self.client, make_property(price_amount_inr=1.40 * CRORE)))

    def test_over_budget_is_tolerated_when_the_rest_of_the_brief_is_met(self):
        """An over-budget price inside the tolerance, for a client who told us
        more: budget is then one requirement among several that this property
        meets, so it stays on the list at the bottom rather than being
        discarded."""
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                                            price_amount_inr=1.08 * CRORE))
        self.assertIsNotNone(result)
        self.assertEqual(result.bucket, MatchBucket.MEDIUM)

    def test_a_slightly_cheaper_property_is_not_rejected_for_being_cheap(self):
        """The half of §2 that still stands: just under the target band is a
        real option, priced a little keenly. It scores lower, and it stays."""
        cheap = score(self.client, make_property(price_amount_inr=62 * LAKH))
        self.assertIsNotNone(cheap)
        self.assertLess(cheap.field_scores["budget"], 1.0)

    def test_a_far_cheaper_property_is_rejected(self):
        """The half that had to change, and the case that prompted it: a ₹39L
        listing against a ₹1cr brief is not a bargain for that buyer, it is a
        different kind of property in a different part of town — and every one
        of them was taking a slot on a hundred-row shortlist that a real match
        then could not get into."""
        brief = scoring.build_brief(self.client, CLIENT_VECTOR)
        self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=39 * LAKH), brief))
        self.assertIsNone(score(self.client, make_property(price_amount_inr=39 * LAKH)))

    def test_the_floor_is_a_ratio_of_the_band_not_a_rupee_amount(self):
        """So one rule fits a ₹30L brief and a ₹5cr one. Both edges sit the
        same fraction outside the band at every price."""
        for ceiling in (30 * LAKH, 1 * CRORE, 5 * CRORE):
            with self.subTest(ceiling=ceiling):
                brief = scoring.build_brief(make_client(budget_max_inr=ceiling), CLIENT_VECTOR)
                floor = ceiling * config.BUDGET_TARGET_BAND * (1 - config.BUDGET_UNDER_TOLERANCE)
                self.assertTrue(scoring.is_eligible(make_property(price_amount_inr=floor * 1.01), brief))
                self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=floor * 0.99), brief))

    def test_an_unpriced_listing_is_never_rejected_by_either_edge(self):
        """Unknown is not "too cheap" and not "too expensive" — it is unknown,
        and §10 prices it as one rather than filtering it out."""
        brief = scoring.build_brief(self.client, CLIENT_VECTOR)
        self.assertTrue(scoring.is_eligible(make_property(), brief))
        self.assertIsNone(score(self.client, make_property()).field_scores["budget"])

    def test_the_tolerances_are_configurable(self):
        with mock.patch.object(config, "BUDGET_OVER_TOLERANCE", 0.0):
            self.assertIsNone(score(self.client, make_property(price_amount_inr=1.01 * CRORE)))
        with mock.patch.object(config, "BUDGET_UNDER_TOLERANCE", 1.0):
            self.assertIsNotNone(score(self.client, make_property(price_amount_inr=20 * LAKH)))


class BudgetCurveTest(unittest.TestCase):
    """§4 — a ceiling is a target, not merely a limit."""

    def test_a_ceiling_only_brief_reads_a_target_band_below_it(self):
        client = make_client(budget_max_inr=1 * CRORE)
        prices = [95 * LAKH, 70 * LAKH, 64 * LAKH, 62 * LAKH, 60 * LAKH]
        scores = [score(client, make_property(price_amount_inr=p)).field_scores["budget"] for p in prices]
        # Strictly decreasing as the price falls away from the band, exactly
        # as §4's worked list says: very strong, strong, moderate, weaker, poor.
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scores[0], 1.0)
        self.assertEqual(scores[1], 1.0)   # ₹70L is INSIDE the band, not below it
        self.assertLess(scores[-1], 0.7)

    def test_a_stated_minimum_is_used_as_written(self):
        """A client who gave both ends never gets an inferred band on either
        side — their own two numbers are the band."""
        client = make_client(budget_min_inr=40 * LAKH, budget_max_inr=1 * CRORE)
        # 50L is below the band a ceiling-only brief would have inferred, and
        # inside this client's own stated range.
        self.assertEqual(score(client, make_property(price_amount_inr=50 * LAKH)).field_scores["budget"], 1.0)
        # ...and their own floor is what the hard edge is measured from.
        brief = scoring.build_brief(client, CLIENT_VECTOR)
        self.assertTrue(scoring.is_eligible(make_property(price_amount_inr=38 * LAKH), brief))
        self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=30 * LAKH), brief))

    def test_a_floor_only_brief_reads_a_target_band_above_it(self):
        """The mirror image, and the half that did not exist. "At least ₹1cr"
        used to have no top at all, so a ₹6cr bungalow scored a perfect 1.0 on
        budget and outranked the ₹1.1cr flat the client actually wanted."""
        client = make_client(budget_min_inr=1 * CRORE)
        self.assertEqual(score(client, make_property(price_amount_inr=1.3 * CRORE)).field_scores["budget"], 1.0)
        brief = scoring.build_brief(client, CLIENT_VECTOR)
        self.assertTrue(scoring.is_eligible(make_property(price_amount_inr=92 * LAKH), brief))
        self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=80 * LAKH), brief))
        self.assertFalse(scoring.is_eligible(make_property(price_amount_inr=6 * CRORE), brief))

    def test_a_reversed_range_is_read_as_the_range_it_describes(self):
        """Real data writes min and max the wrong way round. Read literally
        that is an empty band, and with a hard floor beneath its low end it
        would now match nothing at all."""
        client = make_client(budget_min_inr=1 * CRORE, budget_max_inr=80 * LAKH)
        self.assertEqual(score(client, make_property(price_amount_inr=90 * LAKH)).field_scores["budget"], 1.0)

    def test_over_budget_loses_score_gradually(self):
        client = make_client(budget_max_inr=1 * CRORE)
        just_over = score(client, make_property(price_amount_inr=1.02 * CRORE)).field_scores["budget"]
        well_over = score(client, make_property(price_amount_inr=1.09 * CRORE)).field_scores["budget"]
        self.assertGreater(just_over, well_over)
        self.assertGreater(just_over, 0.8)


class LocationTest(unittest.TestCase):
    """§5 — levels of geographic relevance, never a yes/no check, and never
    invented."""

    def tier(self, client_areas: str, **property_location) -> Optional[float]:
        """The tier function itself. Asserted directly rather than through a
        whole score, because a location-only brief's total is dominated by the
        location — a far-off property then falls below the display floor and
        there is no field score left to inspect. The end-to-end wiring is
        covered by test_the_tier_is_what_the_score_uses below."""
        return normalization.location_score(
            normalization.client_area_tokens(client_areas),
            property_location.get("area_name"),
            property_location.get("address"),
            property_location.get("society_name"),
        )

    def test_the_tier_is_what_the_score_uses(self):
        client = make_client(preferred_areas="Vesu", budget_max_inr=1 * CRORE)
        result = score(client, make_property(area_name="Vesu", price_amount_inr=95 * LAKH))
        self.assertEqual(result.field_scores["location"], config.LOCATION_EXACT)
        self.assertIn("location", result.matched_requirements)

    def test_requested_locality_is_the_top_tier(self):
        self.assertEqual(self.tier("Vesu", area_name="Vesu"), config.LOCATION_EXACT)
        self.assertEqual(self.tier("Vesu", area_name="Vesu Road"), config.LOCATION_EXACT)

    def test_a_distinctive_word_of_the_locality_is_a_partial_hit(self):
        self.assertEqual(self.tier("Vesu Road", area_name="Vesu"), config.LOCATION_PARTIAL)

    def test_a_near_spelling_is_a_fuzzy_hit(self):
        self.assertEqual(self.tier("Piplod", area_name="Pipplod"), config.LOCATION_FUZZY)

    def test_an_area_name_inside_a_longer_WORD_is_not_a_hit(self):
        """The case from the real data: "Pal" and "Palanpur Patiya" are two
        different parts of the city, and a plain substring test called them
        the same place and handed it the TOP tier — the one thing location
        scoring must never get wrong."""
        self.assertEqual(self.tier("Pal", area_name="Palanpur Patiya"), config.LOCATION_OTHER)
        self.assertEqual(self.tier("Pal", address="Palanpur Jakatnaka, Surat"), config.LOCATION_OTHER)

    def test_an_area_name_that_is_a_whole_word_of_a_longer_NAME_is_a_hit(self):
        """...and the reason the fix is a word boundary rather than dropping
        substring matching: "Sarthana" really is inside "Sarthana Jakatnaka"
        and really is the same neighbourhood. Both halves of this pair have to
        hold, or one of them breaks the other."""
        self.assertEqual(self.tier("Sarthana", area_name="Sarthana Jakatnaka"), config.LOCATION_EXACT)
        self.assertEqual(self.tier("Pal", area_name="Pal Gam"), config.LOCATION_EXACT)
        self.assertEqual(self.tier("Pal", address="Adajan Pal Road, Surat"), config.LOCATION_EXACT)

    def test_punctuation_does_not_decide_whether_two_places_are_the_same(self):
        """Words, not characters: the two sides may be spaced and punctuated
        however whoever typed them felt like."""
        self.assertEqual(self.tier("A.K. Road", area_name="A K Road, Surat"), config.LOCATION_EXACT)
        self.assertEqual(self.tier("Vesu-Abhva", area_name="Vesu Abhva"), config.LOCATION_EXACT)

    def test_a_short_area_name_is_never_matched_by_near_spelling(self):
        """"Pal" and "Pali" are 0.86 alike and are two real, different areas.
        A near-spelling guess is only meaningful on a word long enough for one
        letter to be a small part of it."""
        self.assertEqual(self.tier("Pal", area_name="Pali"), config.LOCATION_OTHER)
        # ...and the long ones still get the benefit of the doubt.
        self.assertEqual(self.tier("Adajan", area_name="Adajann"), config.LOCATION_FUZZY)

    def test_a_shared_filler_word_is_not_a_location_match(self):
        """"Adajan Gam" and "Pal Gam" share a word and are different places.
        Without the stop-word list this would read as a partial hit."""
        self.assertEqual(self.tier("Adajan Gam", area_name="Pal Gam"), config.LOCATION_OTHER)

    def test_a_configured_neighbouring_area_scores_below_an_exact_hit(self):
        with mock.patch.object(config, "NEARBY_AREAS", {"vesu": ("piplod",)}):
            self.assertEqual(self.tier("Vesu", area_name="Piplod"), config.LOCATION_NEARBY)

    def test_no_adjacency_is_ever_invented(self):
        """The default configuration claims no geography at all, so two
        unrelated localities are simply unrelated."""
        self.assertEqual(config.NEARBY_AREAS, {})
        self.assertEqual(self.tier("Vesu", area_name="Piplod"), config.LOCATION_OTHER)

    def test_same_city_different_locality_scores_in_the_middle(self):
        tier = self.tier("Vesu, Surat", area_name="Adajan", address="Adajan, Surat")
        self.assertEqual(tier, config.LOCATION_SAME_CITY)
        self.assertLess(tier, config.LOCATION_NEARBY)
        self.assertGreater(tier, config.LOCATION_OTHER)

    def test_a_city_named_on_its_own_is_the_requested_area(self):
        """Worked example A's client said only "Ahmedabad" — a listing in
        Ahmedabad is what they asked for, not a consolation tier."""
        self.assertEqual(self.tier("Ahmedabad", area_name="Ahmedabad"), config.LOCATION_EXACT)

    def test_a_far_away_property_scores_the_bottom_tier(self):
        self.assertEqual(self.tier("Vesu, Surat", area_name="Bodakdev", address="Ahmedabad"), config.LOCATION_OTHER)

    def test_the_society_name_counts_as_location_text(self):
        self.assertEqual(self.tier("Black Residency", society_name="Black Residency"), config.LOCATION_EXACT)

    def test_unstated_location_is_never_a_penalty(self):
        client = make_client(budget_max_inr=1 * CRORE)
        result = score(client, make_property(price_amount_inr=90 * LAKH, area_name="anywhere"))
        self.assertNotIn("location", result.field_scores)

    def test_tiers_are_ordered_as_documented(self):
        self.assertGreater(config.LOCATION_EXACT, config.LOCATION_PARTIAL)
        self.assertGreater(config.LOCATION_PARTIAL, config.LOCATION_FUZZY)
        self.assertGreater(config.LOCATION_FUZZY, config.LOCATION_NEARBY)
        self.assertGreater(config.LOCATION_NEARBY, config.LOCATION_SAME_CITY)
        self.assertGreater(config.LOCATION_SAME_CITY, config.LOCATION_OTHER)


class SizeTest(unittest.TestCase):
    """§8 — and §10's rule applied to it."""

    def client(self):
        return make_client(property_type="Flat", property_sizes={"Flat": "1200 sqft"})

    def test_a_size_inside_the_requested_range_scores_full(self):
        result = score(self.client(), make_property(property_type="Flat", area_sqft=1200))
        self.assertEqual(result.field_scores["size"], 1.0)

    def test_a_size_outside_the_range_loses_score_gradually(self):
        near = score(self.client(), make_property(property_type="Flat", area_sqft=1400)).field_scores["size"]
        far = score(self.client(), make_property(property_type="Flat", area_sqft=2400)).field_scores["size"]
        self.assertGreater(near, far)
        self.assertGreater(far, 0.0)

    def test_a_listing_with_no_size_is_unknown_not_a_perfect_match(self):
        """§8, stated outright: "Mark size as UNKNOWN rather than treating it
        as a perfect match.\""""
        result = score(self.client(), make_property(property_type="Flat"))
        self.assertIsNone(result.field_scores["size"])
        self.assertIn("size", result.missing_information)
        self.assertNotIn("size", result.matched_requirements)

    def test_a_missing_size_scores_below_a_matching_one(self):
        known = score(self.client(), make_property(property_type="Flat", area_sqft=1200))
        unknown = score(self.client(), make_property(property_type="Flat"))
        self.assertLess(unknown.score, known.score)

    def test_unstated_size_is_never_scored(self):
        result = score(make_client(property_type="Flat"), make_property(property_type="Flat", area_sqft=400))
        self.assertNotIn("size", result.field_scores)


class FurnishingTest(unittest.TestCase):
    """§9."""

    def client(self):
        return make_client(furnishing=normalization.FULLY_FURNISHED)

    def test_the_same_level_scores_full(self):
        result = score(self.client(), make_property(furnishing=normalization.FULLY_FURNISHED))
        self.assertEqual(result.field_scores["furnishing"], 1.0)

    def test_one_level_away_is_moderate_and_two_is_low(self):
        one = score(self.client(), make_property(furnishing=normalization.SEMI_FURNISHED)).field_scores["furnishing"]
        two = score(self.client(), make_property(furnishing=normalization.UNFURNISHED)).field_scores["furnishing"]
        self.assertLess(one, 1.0)
        self.assertLess(two, one)

    def test_a_listing_that_does_not_say_is_unknown(self):
        result = score(self.client(), make_property())
        self.assertIsNone(result.field_scores["furnishing"])
        self.assertIn("furnishing", result.missing_information)

    def test_unstated_furnishing_is_never_scored(self):
        result = score(make_client(budget_max_inr=1 * CRORE), make_property(price_amount_inr=90 * LAKH))
        self.assertNotIn("furnishing", result.field_scores)

    def test_furnishing_never_moves_a_good_match_out_of_its_bucket(self):
        """It is the lowest-weighted field on purpose — a tie-breaker."""
        client = make_client(
            purpose="buy", property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
            budget_max_inr=1 * CRORE, furnishing=normalization.FULLY_FURNISHED,
        )
        prop = dict(property_type="Flat", bhk="3 BHK", area_name="Vesu", price_amount_inr=95 * LAKH)
        good = score(client, make_property(furnishing=normalization.FULLY_FURNISHED, **prop))
        worse = score(client, make_property(furnishing=normalization.UNFURNISHED, **prop))
        self.assertEqual(good.bucket, MatchBucket.HIGH)
        self.assertEqual(worse.bucket, MatchBucket.HIGH)
        self.assertLess(worse.score, good.score)


class MissingPropertyInformationTest(unittest.TestCase):
    """§10 and §17 — the most consequential rules here. A requirement the
    client stated and the LISTING cannot answer is an unknown: never a
    perfect match, never silently dropped."""

    def full_brief(self):
        return make_client(
            purpose="buy", property_type="Flat", bhk="3 BHK",
            preferred_areas="Ahmedabad", budget_max_inr=1.5 * CRORE,
        )

    def test_example_d_a_listing_with_no_price(self):
        client = self.full_brief()
        priced = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Ahmedabad",
                                            price_amount_inr=1.4 * CRORE))
        unpriced = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Ahmedabad"))

        self.assertIsNotNone(unpriced)                            # still eligible
        self.assertIsNone(unpriced.field_scores["budget"])        # budget = UNKNOWN
        self.assertEqual(unpriced.missing_information, ["budget"])
        self.assertLess(unpriced.score, priced.score)             # score reflects it
        self.assertLess(unpriced.confidence_score, priced.confidence_score)  # confidence reduced

    def test_a_missing_price_is_not_a_perfect_budget_match(self):
        result = score(self.full_brief(), make_property(property_type="Flat", bhk="3 BHK", area_name="Ahmedabad"))
        self.assertNotEqual(result.field_scores["budget"], 1.0)
        self.assertNotIn("budget", result.matched_requirements)
        self.assertIn("Listing does not say what it costs", result.reasons)

    def test_a_missing_bhk_is_unknown(self):
        result = score(self.full_brief(), make_property(property_type="Flat", area_name="Ahmedabad",
                                                       price_amount_inr=1.4 * CRORE))
        self.assertIsNone(result.field_scores["bhk"])
        self.assertIn("bhk", result.missing_information)

    def test_a_missing_location_is_unknown(self):
        result = score(self.full_brief(), make_property(property_type="Flat", bhk="3 BHK",
                                                       price_amount_inr=1.4 * CRORE))
        self.assertIsNone(result.field_scores["location"])
        self.assertIn("location", result.missing_information)

    def test_an_unknown_field_does_not_reweight_the_others_up_to_full_marks(self):
        """The bug this rule exists to prevent: dropping an unanswerable field
        from the average leaves a listing that answered only ONE requirement
        scoring as though it had answered them all."""
        answers_nothing = score(self.full_brief(), make_property(property_type="Flat"))
        answers_everything = score(
            self.full_brief(),
            make_property(property_type="Flat", bhk="3 BHK", area_name="Ahmedabad",
                          price_amount_inr=1.4 * CRORE),
        )
        self.assertLess(answers_nothing.score, answers_everything.score)
        self.assertLess(answers_nothing.evidence_ratio, answers_everything.evidence_ratio)

    def test_a_requirement_the_client_never_stated_is_absent_not_null(self):
        """"Never asked about" and "asked about and unknown" are opposite
        facts, and the stored row must not spell them the same way."""
        result = score(make_client(budget_max_inr=1 * CRORE), make_property(price_amount_inr=90 * LAKH))
        self.assertNotIn("bhk", result.field_scores)
        self.assertNotIn("location", result.field_scores)
        self.assertEqual(result.missing_information, [])


class SoftMatchScoreTest(unittest.TestCase):
    """§11 — the denominator is what the CLIENT asked for."""

    def test_the_denominator_follows_the_brief_not_the_listing(self):
        """§11's own example: location must not gain weight merely because
        the property happens to have an address."""
        budget_only = make_client(budget_max_inr=1 * CRORE)
        bare = score(budget_only, make_property(price_amount_inr=95 * LAKH))
        detailed = score(
            budget_only,
            make_property(price_amount_inr=95 * LAKH, area_name="Somewhere Else", bhk="1 BHK",
                          property_type="Shop", furnishing=normalization.UNFURNISHED),
        )
        # Identical price, identical description similarity, and the client
        # asked about nothing else — so the two must score the same.
        self.assertEqual(bare.score, detailed.score)

    def test_default_weights_match_the_specification(self):
        self.assertEqual(config.MATCH_WEIGHTS["budget"], 0.35)
        self.assertEqual(config.MATCH_WEIGHTS["location"], 0.30)
        self.assertEqual(config.MATCH_WEIGHTS["bhk"], 0.20)
        self.assertEqual(config.MATCH_WEIGHTS["semantic"], 0.16)

    def test_the_weights_keep_their_documented_order(self):
        """The calibration contract, so a later tweak to one number cannot
        quietly reorder what this engine thinks matters. Budget stays the
        heaviest single field; semantic stays behind all three of the
        structured ones however much it is raised."""
        weights = config.MATCH_WEIGHTS
        self.assertGreater(weights["budget"], weights["location"])
        self.assertGreater(weights["location"], weights["bhk"])
        self.assertGreater(weights["bhk"], weights["semantic"])
        self.assertGreater(weights["semantic"], weights["property_type"])

    def test_property_type_cannot_overpower_the_score(self):
        """§11 — it stays primarily a hard compatibility check. A compatible
        (not exact) type must not push a strong match out of High."""
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE)
        compatible = score(client, make_property(property_type="Apartment", bhk="3 BHK",
                                                 area_name="Vesu", price_amount_inr=95 * LAKH))
        self.assertEqual(compatible.bucket, MatchBucket.HIGH)

    def test_example_a_a_complete_brief_met_in_full(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Ahmedabad", budget_max_inr=1.5 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK",
                                             area_name="Ahmedabad", price_amount_inr=1.4 * CRORE))
        self.assertEqual(result.bucket, MatchBucket.HIGH)
        self.assertEqual(result.confidence_bucket, MatchBucket.HIGH)
        self.assertGreater(result.score, 0.95)
        self.assertEqual(result.missing_information, [])
        self.assertEqual(
            sorted(result.matched_requirements),
            sorted(["budget", "location", "bhk", "property_type", "purpose"]),
        )


class SemanticSimilarityTest(unittest.TestCase):
    """§12 — a supporting signal that can never override a structured
    requirement."""

    def test_a_perfect_description_match_cannot_rescue_the_wrong_type(self):
        client = make_client(property_type="Flat", bhk="3 BHK")
        bungalow = make_property(property_type="Bungalow", bhk="3 BHK", embedding=IDENTICAL)
        self.assertIsNone(score(client, bungalow))

    def test_a_perfect_description_match_cannot_rescue_the_wrong_purpose(self):
        client = make_client(purpose="rent")
        self.assertIsNone(score(client, make_property(listing_type="Sale", embedding=IDENTICAL)))

    def test_a_perfect_description_match_cannot_rescue_a_ruled_out_bhk(self):
        client = make_client(bhk="exactly 3 BHK")
        self.assertIsNone(score(client, make_property(bhk="4 BHK", embedding=IDENTICAL)))

    def test_a_perfect_description_match_cannot_rescue_a_price_far_over_budget(self):
        client = make_client(budget_max_inr=1 * CRORE)
        self.assertIsNone(score(client, make_property(price_amount_inr=2 * CRORE, embedding=IDENTICAL)))

    def test_it_stays_a_low_weight_signal(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE)
        prop = dict(property_type="Flat", bhk="3 BHK", area_name="Vesu", price_amount_inr=95 * LAKH)
        best = score(client, make_property(embedding=IDENTICAL, **prop))
        worst = score(client, make_property(embedding=UNRELATED, **prop))
        # It moves the score, but never far enough to change what a property
        # that matches every stated requirement is worth.
        self.assertGreater(best.score, worst.score)
        self.assertEqual(best.bucket, MatchBucket.HIGH)
        self.assertEqual(worst.bucket, MatchBucket.HIGH)

    def test_a_listing_with_no_vector_is_unknown_not_a_mismatch(self):
        client = make_client(budget_max_inr=1 * CRORE)
        result = score(client, make_property(price_amount_inr=95 * LAKH, embedding=[]))
        self.assertIsNone(result.field_scores["semantic"])


class CoreRequirementCeilingTest(unittest.TestCase):
    """§6's "do not let budget or location compensate", applied to every core
    requirement. Found against the real dataset: a client who asked for a
    4 BHK Flat in Green City under ₹90L was being shown 3 BHK flats at "88%,
    High match, high confidence, nothing missing"."""

    def client(self):
        return make_client(property_type="Flat", bhk="4 BHK", preferred_areas="Green City",
                           budget_max_inr=90 * LAKH)

    def test_a_wrong_bhk_cannot_be_a_high_match(self):
        """The exact case from the data: everything else perfect."""
        result = score(self.client(), make_property(property_type="Flat", bhk="3 BHK",
                                                   area_name="Green City", price_amount_inr=60 * LAKH))
        self.assertIsNotNone(result)
        self.assertNotEqual(result.bucket, MatchBucket.HIGH)
        self.assertEqual(result.bucket, MatchBucket.MEDIUM)
        self.assertNotIn("bhk", result.matched_requirements)
        self.assertIn("A different BHK from the one requested", result.reasons)

    def test_the_right_bhk_is_not_capped(self):
        result = score(self.client(), make_property(property_type="Flat", bhk="4 BHK",
                                                   area_name="Green City", price_amount_inr=60 * LAKH))
        self.assertEqual(result.bucket, MatchBucket.HIGH)

    def test_a_wrong_area_cannot_be_a_high_match(self):
        client = make_client(property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
                             budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK",
                                            area_name="Somewhere Unrelated", price_amount_inr=95 * LAKH))
        self.assertIsNotNone(result)
        self.assertNotEqual(result.bucket, MatchBucket.HIGH)

    def test_over_budget_cannot_be_a_high_match(self):
        """As over budget as a property may now be and still be eligible at
        all — everything else about it perfect."""
        client = make_client(property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
                             budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                                            price_amount_inr=1.09 * CRORE))
        self.assertIsNotNone(result)
        self.assertNotEqual(result.bucket, MatchBucket.HIGH)

    def test_under_budget_cannot_be_a_high_match_either(self):
        """The same rule on the side that used to have no rule: a price at the
        bottom edge of what is still eligible is a missed requirement, and a
        perfect area, type and BHK may not pay for it."""
        client = make_client(property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
                             budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                                            price_amount_inr=60 * LAKH))
        self.assertIsNotNone(result)
        self.assertNotEqual(result.bucket, MatchBucket.HIGH)

    def test_a_compatible_type_is_not_capped_out_of_high(self):
        """The ceiling must not punish the compatibility the engine allows on
        purpose — worked example F has to stay a strong match."""
        client = make_client(property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
                             budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Apartment", bhk="3 BHK",
                                            area_name="Vesu", price_amount_inr=95 * LAKH))
        self.assertEqual(result.bucket, MatchBucket.HIGH)

    def test_an_unknown_is_not_treated_as_a_miss(self):
        """Unknown is not wrong, and the ceiling must not collapse the
        difference — that distinction is what the whole design rests on."""
        client = make_client(property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
                             budget_max_inr=1 * CRORE)
        unknown_bhk = score(client, make_property(property_type="Flat", area_name="Vesu",
                                                 price_amount_inr=95 * LAKH))
        wrong_bhk = score(client, make_property(property_type="Flat", bhk="2 BHK", area_name="Vesu",
                                               price_amount_inr=95 * LAKH))
        self.assertIsNone(unknown_bhk.field_scores["bhk"])
        self.assertGreater(unknown_bhk.score, wrong_bhk.score)

    def test_a_thin_brief_that_is_fully_met_is_never_capped(self):
        """The ceiling keys on requirements NOT MET, never on requirements not
        given — that is the line §15 draws, and worked example C depends on
        it."""
        result = score(make_client(budget_max_inr=1 * CRORE), make_property(price_amount_inr=95 * LAKH))
        self.assertEqual(result.bucket, MatchBucket.HIGH)

    def test_the_ceiling_curve_is_monotonic_and_configurable(self):
        ceilings = [value for _, value in config.CORE_MISS_CEILINGS]
        self.assertEqual(ceilings, sorted(ceilings))
        self.assertEqual(config.CORE_MISS_CEILINGS[-1], (1.00, 1.00))
        with mock.patch.object(config, "CORE_MISS_CEILINGS", ((0.0, 1.0), (1.0, 1.0))):
            uncapped = score(self.client(), make_property(property_type="Flat", bhk="3 BHK",
                                                          area_name="Green City",
                                                          price_amount_inr=60 * LAKH))
            self.assertEqual(uncapped.bucket, MatchBucket.HIGH)


class SemanticCannotStandAloneTest(unittest.TestCase):
    """§12 — embedding similarity is a supporting signal, so it may never be
    the whole of a score. Found against the real dataset: one client's only
    stored requirement is the word "investment"."""

    def test_a_client_with_no_structured_requirement_matches_nothing(self):
        client = make_client(purpose="investment")
        # It passes has_requirements (purpose is set), so it IS scored today —
        # and must come back with nothing rather than an embedding ranking.
        self.assertTrue(matching_service.has_requirements(client))
        self.assertIsNone(score(client, make_property(embedding=IDENTICAL)))

    def test_free_text_alone_matches_nothing(self):
        client = make_client(additional_requirements="looking for something nice")
        self.assertTrue(matching_service.has_requirements(client))
        self.assertIsNone(score(client, make_property(embedding=IDENTICAL)))

    def test_one_structured_requirement_is_enough_to_be_matched(self):
        client = make_client(budget_max_inr=1 * CRORE)
        self.assertIsNotNone(score(client, make_property(price_amount_inr=95 * LAKH)))

    def test_a_recognised_purpose_counts_as_structured(self):
        """"buy" is a real requirement and filters real properties; the
        unrecognised "investment" is not."""
        self.assertTrue(scoring.build_brief(make_client(purpose="buy"), CLIENT_VECTOR).has_structured_requirement)
        self.assertFalse(
            scoring.build_brief(make_client(purpose="investment"), CLIENT_VECTOR).has_structured_requirement
        )


class ConfidenceTest(unittest.TestCase):
    """§1, §13, §15, §19 — confidence is a SEPARATE number and never lowers
    the match score."""

    def test_example_c_high_match_low_confidence(self):
        """A ₹95L property against "up to ₹1 Cr" is a very good match for the
        one thing we were told, and we were told very little. Both facts are
        reported; neither is allowed to damage the other."""
        client = make_client(budget_max_inr=1 * CRORE)
        result = score(client, make_property(price_amount_inr=95 * LAKH))
        self.assertEqual(result.field_scores["budget"], 1.0)
        self.assertGreaterEqual(result.score, config.MATCH_HIGH_CUTOFF)
        self.assertEqual(result.bucket, MatchBucket.HIGH)
        self.assertEqual(result.confidence_bucket, MatchBucket.LOW)

    def test_confidence_grows_as_the_brief_grows(self):
        prop = make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                             price_amount_inr=95 * LAKH)
        briefs = [
            make_client(budget_max_inr=1 * CRORE),
            make_client(budget_max_inr=1 * CRORE, preferred_areas="Vesu"),
            make_client(budget_max_inr=1 * CRORE, preferred_areas="Vesu", property_type="Flat"),
            make_client(budget_max_inr=1 * CRORE, preferred_areas="Vesu", property_type="Flat", bhk="3 BHK"),
        ]
        confidences = [score(client, prop).confidence_score for client in briefs]
        self.assertEqual(confidences, sorted(confidences))
        self.assertLess(confidences[0], config.CONFIDENCE_MEDIUM_CUTOFF)
        self.assertGreaterEqual(confidences[-1], config.CONFIDENCE_HIGH_CUTOFF)

    def test_a_thin_brief_never_lowers_the_match_score(self):
        """The rule this redesign exists for. The same property, against a
        one-line brief and against a complete one that it also fits perfectly:
        the thin brief's match score must not be pushed down."""
        prop = make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                             price_amount_inr=95 * LAKH)
        thin = score(make_client(budget_max_inr=1 * CRORE), prop)
        complete = score(
            make_client(purpose="buy", budget_max_inr=1 * CRORE, preferred_areas="Vesu",
                        property_type="Flat", bhk="3 BHK"),
            prop,
        )
        self.assertEqual(thin.bucket, MatchBucket.HIGH)
        self.assertEqual(complete.bucket, MatchBucket.HIGH)
        self.assertLess(thin.confidence_score, complete.confidence_score)

    def test_specificity_weights_match_the_specification(self):
        self.assertEqual(config.CONFIDENCE_WEIGHTS["budget"], 0.32)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["location"], 0.24)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["property_type"], 0.20)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["bhk"], 0.16)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["semantic"], 0.08)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["size"], 0.08)
        self.assertEqual(config.CONFIDENCE_WEIGHTS["furnishing"], 0.06)

    def test_the_cutoffs_sit_exactly_where_the_brief_sizes_do(self):
        """The calibration contract, checked against the weights rather than
        assumed: ANY two of the four core requirements must reach Medium, and
        any three including a budget must reach High. A round cutoff near
        those sums would drop the weakest qualifying brief on one side of the
        line for no reason a broker could explain — and the weakest two-field
        brief, budget + BHK, is the second most common one in this business."""
        weights = config.CONFIDENCE_WEIGHTS
        others = ("location", "property_type", "bhk")

        # Budget plus any one other core requirement reaches Medium. The
        # weakest is budget + BHK (0.48), which is the cutoff exactly.
        for name in others:
            with self.subTest(brief=("budget", name)):
                self.assertGreaterEqual(weights["budget"] + weights[name], config.CONFIDENCE_MEDIUM_CUTOFF)

        # Budget plus any two others reaches High. The weakest is
        # budget + property type + BHK (0.68), which is the cutoff exactly.
        for i, a in enumerate(others):
            for b in others[i + 1 :]:
                with self.subTest(brief=("budget", a, b)):
                    self.assertGreaterEqual(
                        weights["budget"] + weights[a] + weights[b], config.CONFIDENCE_HIGH_CUTOFF
                    )

        # One requirement on its own never reaches Medium, or "confidence"
        # would stop meaning anything.
        for name in ("budget", *others):
            with self.subTest(brief=name):
                self.assertLess(weights[name], config.CONFIDENCE_MEDIUM_CUTOFF)

    def test_a_brief_with_no_budget_stays_low_confidence(self):
        """A deliberate consequence of the specification's own weights, stated
        here so it is a decision rather than a surprise: budget carries the
        most specificity, so "Flat, 3 BHK" with no budget at all (0.36) is Low
        confidence. Nothing useful can be said to such a client about price,
        and price is what almost every one of these conversations turns on.
        Raise CONFIDENCE_WEIGHTS["property_type"]/["bhk"] if this business
        judges otherwise — that is what the table is for."""
        weights = config.CONFIDENCE_WEIGHTS
        self.assertLess(weights["property_type"] + weights["bhk"], config.CONFIDENCE_MEDIUM_CUTOFF)
        client = make_client(property_type="Flat", bhk="3 BHK")
        result = score(client, make_property(property_type="Flat", bhk="3 BHK"))
        self.assertEqual(result.confidence_bucket, MatchBucket.LOW)

    def test_budget_and_bhk_reaches_medium_confidence(self):
        """The case the round cutoff used to miss by two hundredths — 57 of
        the 455 real clients in this database give exactly this brief."""
        client = make_client(budget_max_inr=1 * CRORE, bhk="3 BHK")
        result = score(client, make_property(bhk="3 BHK", price_amount_inr=95 * LAKH))
        self.assertEqual(result.confidence_bucket, MatchBucket.MEDIUM)

    def test_budget_type_and_bhk_reaches_high_confidence(self):
        """We know what they want and what they can spend; only the area is
        open. That is a brief a broker can act on."""
        client = make_client(budget_max_inr=1 * CRORE, property_type="Flat", bhk="3 BHK")
        result = score(client, make_property(property_type="Flat", bhk="3 BHK", price_amount_inr=95 * LAKH))
        self.assertEqual(result.confidence_bucket, MatchBucket.HIGH)

    def test_confidence_never_exceeds_one(self):
        client = make_client(
            purpose="buy", property_type="Flat", bhk="3 BHK", preferred_areas="Vesu",
            budget_max_inr=1 * CRORE, furnishing=normalization.FULLY_FURNISHED,
            property_sizes={"Flat": "1200 sqft"}, additional_requirements="south facing, veg family",
        )
        result = score(client, make_property(property_type="Flat", bhk="3 BHK", area_name="Vesu",
                                             price_amount_inr=95 * LAKH, area_sqft=1200,
                                             furnishing=normalization.FULLY_FURNISHED))
        self.assertLessEqual(result.confidence_score, 1.0)
        self.assertEqual(result.confidence_bucket, MatchBucket.HIGH)

    def test_free_text_is_what_earns_the_semantic_confidence_weight(self):
        """Every client with any requirement has a vector, so the vector's
        mere existence must not count as evidence about what they want."""
        prop = make_property(price_amount_inr=95 * LAKH)
        without = score(make_client(budget_max_inr=1 * CRORE), prop)
        with_text = score(
            make_client(budget_max_inr=1 * CRORE, additional_requirements="prefers a high floor"), prop
        )
        self.assertEqual(without.confidence_score, config.CONFIDENCE_WEIGHTS["budget"])
        self.assertGreater(with_text.confidence_score, without.confidence_score)
        # ...and the match score is untouched by it.
        self.assertEqual(without.score, with_text.score)


class BucketTest(unittest.TestCase):
    """§15 — buckets are read off the real score, never produced by bending
    it."""

    def test_cutoffs_match_the_specification(self):
        self.assertEqual(config.MATCH_HIGH_CUTOFF, 0.85)
        self.assertEqual(config.MATCH_MEDIUM_CUTOFF, 0.65)

    def test_buckets_follow_the_score(self):
        self.assertEqual(scoring.match_bucket(0.92), MatchBucket.HIGH)
        self.assertEqual(scoring.match_bucket(0.85), MatchBucket.HIGH)
        self.assertEqual(scoring.match_bucket(0.84), MatchBucket.MEDIUM)
        self.assertEqual(scoring.match_bucket(0.65), MatchBucket.MEDIUM)
        self.assertEqual(scoring.match_bucket(0.64), MatchBucket.LOW)

    def test_cutoffs_are_configurable(self):
        with mock.patch.object(config, "MATCH_HIGH_CUTOFF", 0.95):
            self.assertEqual(scoring.match_bucket(0.92), MatchBucket.MEDIUM)

    def test_the_percentage_shown_is_the_score_that_chose_the_bucket(self):
        """Nothing rescales a score after its bucket is decided, so a card
        reading 92% can never sit in the Low section."""
        client = make_client(budget_max_inr=1 * CRORE)
        result = score(client, make_property(price_amount_inr=95 * LAKH))
        self.assertEqual(result.bucket, scoring.match_bucket(result.score))


class RankingTest(unittest.TestCase):
    """§16."""

    def match(self, score_value: float, confidence: float, fields=None) -> MatchScore:
        return MatchScore(
            record_id=f"{score_value}-{confidence}",
            score=score_value,
            bucket=scoring.match_bucket(score_value),
            confidence_score=confidence,
            evidence_ratio=1.0,
            is_partial_match=False,
            property_category="main",
            field_scores=fields or {"budget": 1.0},
            reason="",
        )

    def test_match_score_leads(self):
        better_fit = self.match(0.90, 0.30)
        better_known = self.match(0.85, 0.95)
        ranked = sorted([better_known, better_fit], key=scoring.ranking_key, reverse=True)
        self.assertEqual(ranked[0], better_fit)

    def test_confidence_breaks_a_tie(self):
        low = self.match(0.90, 0.30)
        high = self.match(0.90, 0.90)
        ranked = sorted([low, high], key=scoring.ranking_key, reverse=True)
        self.assertEqual(ranked[0], high)

    def test_exactness_breaks_a_remaining_tie(self):
        approximate = self.match(0.90, 0.50, {"budget": 0.9, "bhk": 0.9})
        exact = self.match(0.90, 0.50, {"budget": 1.0, "bhk": 1.0})
        ranked = sorted([approximate, exact], key=scoring.ranking_key, reverse=True)
        self.assertEqual(ranked[0], exact)

    def test_more_information_never_outranks_a_better_match(self):
        """§16's explicit prohibition, at the widest gap it can occur over."""
        thin_but_excellent = self.match(0.98, 0.32)
        rich_but_mediocre = self.match(0.70, 1.0)
        ranked = sorted([rich_but_mediocre, thin_but_excellent], key=scoring.ranking_key, reverse=True)
        self.assertEqual(ranked[0], thin_but_excellent)


class MaxMatchesTest(unittest.TestCase):
    """§18 — the ceiling keeps the BEST, never an arbitrary hundred."""

    def matches(self, count: int) -> List[MatchScore]:
        return [
            MatchScore(
                record_id=f"p{index}",
                # Descending, so the expected winners are unambiguous.
                score=round(0.99 - index * 0.001, 4),
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

    def test_the_ceiling_is_one_hundred_and_configurable(self):
        self.assertEqual(config.MAX_MATCHES_PER_CLIENT, 100)
        self.assertEqual(matching_service.MAX_MATCHES_PER_CLIENT, config.MAX_MATCHES_PER_CLIENT)

    def test_only_the_highest_ranked_are_kept(self):
        kept = matching_service._best_matches(self.matches(150))
        self.assertEqual(len(kept), config.MAX_MATCHES_PER_CLIENT)
        self.assertEqual({match.record_id for match in kept}, {f"p{i}" for i in range(100)})

    def test_a_shorter_list_is_returned_whole(self):
        kept = matching_service._best_matches(self.matches(7))
        self.assertEqual(len(kept), 7)

    def test_nothing_is_selected_at_random(self):
        """Run twice over a shuffled list — the same hundred every time."""
        import random

        first = self.matches(150)
        random.Random(1).shuffle(first)
        second = self.matches(150)
        random.Random(2).shuffle(second)
        self.assertEqual(
            {m.record_id for m in matching_service._best_matches(first)},
            {m.record_id for m in matching_service._best_matches(second)},
        )


class ExplanationTest(unittest.TestCase):
    """§14 — what a match reports about itself."""

    def test_a_match_carries_the_full_result_structure(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Ahmedabad", budget_max_inr=1.5 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="3 BHK",
                                             area_name="Ahmedabad", price_amount_inr=1.4 * CRORE))
        payload = result.model_dump()
        for key in (
            "score", "confidence_score", "bucket", "confidence_bucket",
            "matched_requirements", "missing_information", "reasons",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["reasons"], result.reason.split("; "))

    def test_no_reason_phrase_contains_a_semicolon(self):
        """The stored form is the phrases joined by "; " and split back
        apart, so a phrase containing one would break the round trip."""
        client = make_client(purpose="buy", property_type="Flat", bhk="2 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE,
                             property_sizes={"Flat": "1200 sqft"},
                             furnishing=normalization.SEMI_FURNISHED)
        for prop in (
            make_property(property_type="Flat", bhk="3 BHK", area_name="Adajan",
                          price_amount_inr=1.1 * CRORE, area_sqft=2000,
                          furnishing=normalization.UNFURNISHED),
            make_property(property_type="Apartment"),
        ):
            result = score(client, prop)
            if result is None:
                continue
            for phrase in result.reasons:
                self.assertNotIn(";", phrase)

    def test_the_derived_lists_agree_with_the_scores(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE)
        result = score(client, make_property(property_type="Flat", bhk="4 BHK", area_name="Vesu"))
        for name in result.matched_requirements:
            self.assertGreaterEqual(result.field_scores[name], config.MATCHED_FIELD_CUTOFF)
        for name in result.missing_information:
            self.assertIsNone(result.field_scores[name])
        self.assertEqual(set(result.matched_requirements) & set(result.missing_information), set())


class NoInventedInformationTest(unittest.TestCase):
    """§17 — nothing is ever assumed, on either side."""

    def test_an_empty_brief_produces_no_matches(self):
        """A client with no requirements is not matched against anything —
        which is what scoring being handed no vector means."""
        self.assertIsNone(score(make_client(), make_property(), vector=[]))

    def test_no_client_field_is_defaulted(self):
        brief = scoring.build_brief(make_client(), CLIENT_VECTOR)
        self.assertFalse(brief.budget_stated)
        self.assertFalse(brief.bhk_stated)
        self.assertFalse(brief.type_stated)
        self.assertFalse(brief.purpose_stated)
        self.assertEqual(brief.area_tokens, [])
        self.assertIsNone(brief.furnishing_level)

    def test_a_listing_missing_everything_scores_no_requirement_as_met(self):
        client = make_client(purpose="buy", property_type="Flat", bhk="3 BHK",
                             preferred_areas="Vesu", budget_max_inr=1 * CRORE,
                             furnishing=normalization.FULLY_FURNISHED)
        result = score(client, make_property(property_type="Flat"))
        self.assertIsNotNone(result)
        self.assertEqual(
            sorted(result.missing_information), sorted(["budget", "location", "bhk", "furnishing"])
        )
        self.assertNotIn("budget", result.matched_requirements)


if __name__ == "__main__":
    unittest.main()
