"""Tests for the field-level hybrid duplicate-detection engine
(Service/duplicate_detection_service.py) and the storage abstraction it
sits on top of (Service/property_vector_store.py).

Deliberately uses the REAL embedding model rather than a mock — the whole
point of this redesign is how it behaves on messy, differently-worded real
text ("Black Residency" vs "Black Recidency"), which a mocked vector could
never actually exercise. The model is already cached locally from earlier
development, so this runs entirely offline.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from Model.duplicate_detection_settings import DuplicateDetectionSettings, FieldWeights, NumericFieldThresholds
from Model.duplicate_verdict import DuplicateVerdict
from Model.embedded_property import EmbeddedProperty
from Model.structured_property import StructuredProperty
from Service import duplicate_detection_service, property_vector_store
from Service.property_pipeline_service import _embed as embed_property


@pytest.fixture(autouse=True)
def _isolated_state():
    """Every test gets an empty store and default settings — these are
    module-level globals, so without this, tests would leak into each
    other."""
    property_vector_store._properties.clear()
    duplicate_detection_service.set_settings(DuplicateDetectionSettings())
    yield
    property_vector_store._properties.clear()
    duplicate_detection_service.set_settings(DuplicateDetectionSettings())


_counter = 0


def make_property(
    society_name: Optional[str] = "Black Residency",
    address: Optional[str] = "120 Feet Road, Vesu",
    area_name: Optional[str] = "Vesu",
    bhk: Optional[str] = "2 BHK",
    property_type: Optional[str] = "Flat",
    carpet_area_sqft: Optional[float] = 1200.0,
    price_text: Optional[str] = "45 Lakh",
    price_amount_inr: Optional[float] = 4_500_000.0,
    contact_phone: Optional[str] = "9876543210",
    description: Optional[str] = "Spacious flat, well ventilated, close to main road.",
) -> EmbeddedProperty:
    global _counter
    _counter += 1
    structured = StructuredProperty(
        source_message_id=f"m{_counter}",
        property_type=property_type,
        bhk=bhk,
        society_name=society_name,
        area_name=area_name,
        address=address,
        carpet_area_sqft=carpet_area_sqft,
        price_text=price_text,
        price_amount_inr=price_amount_inr,
        contact_name="Ramesh",
        contact_phone=contact_phone,
        description=description,
        group_name="Surat Property Deals",
        chat_type="group",
        sender_name="Ramesh Broker",
        sender_saved_name="Ramesh (Broker)",
        sender_phone="+919999999999",
        message_text="raw message text",
        message_timestamp=datetime.now(timezone.utc),
    )
    embedded = embed_property(structured)
    assert embedded is not None
    return embedded


def store(prop: EmbeddedProperty) -> None:
    property_vector_store.add_property(prop)


# --- 1-2: exact repost / reworded --------------------------------------------


def test_exact_repost_is_duplicate():
    store(make_property())
    result = duplicate_detection_service.check_duplicate(make_property())
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


def test_reworded_same_facts_is_duplicate():
    store(make_property(description="Spacious flat, well ventilated, close to main road."))
    result = duplicate_detection_service.check_duplicate(
        make_property(description="Airy, spacious flat located near the main road.")
    )
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


# --- 3-4: society name spelling / abbreviation variation ---------------------


def test_society_spelling_variation_is_duplicate():
    store(make_property(society_name="Black Residency"))
    result = duplicate_detection_service.check_duplicate(make_property(society_name="Black Recidency"))
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


def test_society_abbreviation_variation_is_at_least_flagged():
    """An abbreviation ("Black Rec") is a harder case than a misspelling —
    less textual/semantic overlap to work with. Not asserting hard
    DUPLICATE here would be dishonest if the real model doesn't support it;
    the safety requirement is that it must never be silently lost or
    thrown away as unrelated (HIGH_CONFIDENCE_NEW), only auto-confirmed or
    escalated for a human to look at."""
    store(make_property(society_name="Black Residency"))
    result = duplicate_detection_service.check_duplicate(make_property(society_name="Black Rec"))
    assert result.verdict in (DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE, DuplicateVerdict.UNCERTAIN)


# --- 5: address formatting variation ------------------------------------------


def test_address_formatting_variation_is_duplicate():
    store(make_property(address="120 Feet Road, Vesu, Surat, Gujarat"))
    result = duplicate_detection_service.check_duplicate(make_property(address="120ft Rd Vesu Surat"))
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


# --- 6, 19: different broker / phone number -----------------------------------


def test_different_broker_phone_is_still_duplicate():
    store(make_property(contact_phone="9876543210"))
    result = duplicate_detection_service.check_duplicate(make_property(contact_phone="9111122233"))
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


# --- 7-8: price differences ----------------------------------------------------


def test_small_price_change_is_not_ruled_out():
    """45L -> 48L: ~6.25% relative difference, inside the price
    close/contradiction band, not a hard conflict."""
    store(make_property(price_text="45 Lakh", price_amount_inr=4_500_000.0))
    result = duplicate_detection_service.check_duplicate(
        make_property(price_text="48 Lakh", price_amount_inr=4_800_000.0)
    )
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_NEW
    assert "price" not in " ".join(result.contradictions)


def test_large_price_difference_is_not_automatic_duplicate():
    """45L -> 55L: ~18% relative difference — above the contradiction
    cutoff, even with everything else about the listing identical."""
    store(make_property(price_text="45 Lakh", price_amount_inr=4_500_000.0))
    result = duplicate_detection_service.check_duplicate(
        make_property(price_text="55 Lakh", price_amount_inr=5_500_000.0)
    )
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE
    assert any("price" in c for c in result.contradictions)


# --- 9-10: carpet area differences ---------------------------------------------


def test_small_area_difference_is_not_ruled_out():
    """1200 -> 1210 sqft: <1% relative difference — well within "close"."""
    store(make_property(carpet_area_sqft=1200.0))
    result = duplicate_detection_service.check_duplicate(make_property(carpet_area_sqft=1210.0))
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_NEW
    assert not any("carpet area" in c for c in result.contradictions)


def test_large_area_difference_is_not_automatic_duplicate():
    """1200 -> 1800 sqft: 33% relative difference — a clear contradiction."""
    store(make_property(carpet_area_sqft=1200.0))
    result = duplicate_detection_service.check_duplicate(make_property(carpet_area_sqft=1800.0))
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE
    assert any("carpet area" in c for c in result.contradictions)


# --- 11, 20: same society, different BHK (different unit) ----------------------


def test_same_society_different_bhk_is_not_duplicate():
    store(make_property(society_name="Black Residency", bhk="2 BHK"))
    result = duplicate_detection_service.check_duplicate(make_property(society_name="Black Residency", bhk="4 BHK"))
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_NEW
    assert any("BHK" in c for c in result.contradictions)


def test_same_society_different_unit_price_and_bhk_not_merged():
    """Same building, but a genuinely different unit (different BHK AND
    price) must not be merged into the existing listing."""
    store(make_property(society_name="Sunrise Heights", bhk="2 BHK", price_amount_inr=4_500_000.0))
    result = duplicate_detection_service.check_duplicate(
        make_property(society_name="Sunrise Heights", bhk="3 BHK", price_amount_inr=6_500_000.0)
    )
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_NEW


# --- 12: same society/address, different locality -------------------------------


def test_same_society_and_address_but_different_locality_not_auto_duplicate():
    store(make_property(society_name="Black Residency", address="120 Feet Road", area_name="Althan"))
    result = duplicate_detection_service.check_duplicate(
        make_property(society_name="Black Residency", address="120 Feet Road", area_name="Bamroli")
    )
    # area_name is a semantic (not hard-veto) field by design, but a real
    # locality mismatch must not be allowed to auto-confirm a duplicate.
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


# --- 13: same society/address, different property type --------------------------


def test_same_society_and_address_but_different_property_type_not_duplicate():
    store(make_property(society_name="Black Residency", address="120 Feet Road", property_type="Flat"))
    result = duplicate_detection_service.check_duplicate(
        make_property(society_name="Black Residency", address="120 Feet Road", property_type="Shop")
    )
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_NEW
    assert any("property type" in c for c in result.contradictions)


# --- 14-16: missing fields must not count as conflicts --------------------------


def test_missing_price_is_not_a_conflict():
    store(make_property(price_text="45 Lakh", price_amount_inr=4_500_000.0))
    result = duplicate_detection_service.check_duplicate(make_property(price_text=None, price_amount_inr=None))
    assert result.field_scores["price"] is None
    assert not any("price" in c for c in result.contradictions)


def test_missing_area_is_not_a_conflict():
    store(make_property(carpet_area_sqft=1200.0))
    result = duplicate_detection_service.check_duplicate(make_property(carpet_area_sqft=None))
    assert result.field_scores["carpet_area_sqft"] is None
    assert not any("carpet area" in c for c in result.contradictions)


def test_missing_bhk_is_not_a_conflict():
    store(make_property(bhk="2 BHK"))
    result = duplicate_detection_service.check_duplicate(make_property(bhk=None))
    assert result.field_scores["bhk"] is None
    assert not any("BHK" in c for c in result.contradictions)


# --- 17: similar wording, genuinely different properties ------------------------


def test_similar_wording_but_different_properties_not_duplicate():
    store(
        make_property(
            society_name="Sunrise Heights",
            area_name="Althan",
            bhk="2 BHK",
            price_amount_inr=4_500_000.0,
            description="Nice flat available for sale, good location, ready to move.",
        )
    )
    result = duplicate_detection_service.check_duplicate(
        make_property(
            society_name="Moonlight Towers",
            area_name="Bamroli",
            bhk="4 BHK",
            price_amount_inr=12_000_000.0,
            description="Nice flat available for sale, good location, ready to move.",
        )
    )
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_NEW


# --- 18: genuinely ambiguous case -> UNCERTAIN -----------------------------------


def test_ambiguous_case_is_uncertain():
    """Everything hard-checkable agrees (BHK, property type), price is
    missing on the new side (so it can't corroborate OR contradict), and
    the free-text fields are only loosely related — not clearly the same
    listing, not clearly different either."""
    store(
        make_property(
            society_name="Green Valley Apartments",
            address="Near City Light Road",
            area_name="Vesu",
            bhk="2 BHK",
            property_type="Flat",
            carpet_area_sqft=None,
            price_text="50 Lakh",
            price_amount_inr=5_000_000.0,
        )
    )
    result = duplicate_detection_service.check_duplicate(
        make_property(
            society_name="Greenview Residency",
            address="Off City Light Road",
            area_name="Vesu",
            bhk="2 BHK",
            property_type="Flat",
            carpet_area_sqft=None,
            price_text=None,
            price_amount_inr=None,
        )
    )
    assert result.verdict == DuplicateVerdict.UNCERTAIN


# --- Missing-data confidence gating (Section 14 "do not become artificially high") --


def test_sparse_evidence_does_not_auto_confirm_even_with_perfect_match():
    """Only BHK is comparable (everything else missing on the new side).
    A perfect BHK match alone must not be enough evidence to auto-confirm
    a duplicate — it should fail the evidence-ratio gate."""
    store(make_property())
    sparse_new = make_property(
        society_name=None,
        address=None,
        area_name=None,
        carpet_area_sqft=None,
        price_text=None,
        price_amount_inr=None,
        property_type=None,
        bhk="2 BHK",
    )
    result = duplicate_detection_service.check_duplicate(sparse_new)
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE
    assert result.evidence_ratio < duplicate_detection_service.get_settings().min_evidence_ratio


# --- Empty store -----------------------------------------------------------------


def test_empty_store_is_high_confidence_new():
    result = duplicate_detection_service.check_duplicate(make_property())
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_NEW
    assert result.matched_source_message_id is None


# --- Retrieval is not the final decision (embedding trap) ------------------------


def test_high_embedding_similarity_with_conflicting_price_is_not_duplicate():
    """Near-identical wording drives raw embedding similarity very high,
    but a genuine price conflict must still block auto-duplicate — the
    core failure mode this whole redesign exists to prevent."""
    store(make_property(description="Spacious 2BHK flat near main road.", price_amount_inr=4_500_000.0))
    result = duplicate_detection_service.check_duplicate(
        make_property(description="Spacious 2BHK flat near main road.", price_amount_inr=5_500_000.0)
    )
    assert result.verdict != DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE
    assert any("price" in c for c in result.contradictions)


# --- Candidate retrieval picks the best-scoring match, not just the closest ------


def test_multiple_candidates_best_field_match_wins():
    store(make_property(society_name="Sunrise Heights", area_name="Althan", bhk="2 BHK", price_amount_inr=4_500_000.0))
    store(make_property(society_name="Sunrise Heights", area_name="Althan", bhk="3 BHK", price_amount_inr=6_500_000.0))
    result = duplicate_detection_service.check_duplicate(
        make_property(society_name="Sunrise Heights", area_name="Althan", bhk="3 BHK", price_amount_inr=6_600_000.0)
    )
    assert result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE


# --- property_vector_store.find_top_candidates ------------------------------------


def test_find_top_candidates_ranked_and_limited():
    props = [make_property(description=f"Listing number {i}") for i in range(5)]
    for p in props:
        store(p)
    query_vector = props[0].embedding
    results = property_vector_store.find_top_candidates(query_vector, k=3)
    assert len(results) == 3
    scores = [score for _prop, score in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0][0].source_message_id == props[0].source_message_id
    assert results[0][1] == pytest.approx(1.0, abs=1e-6)


def test_find_top_candidates_empty_store():
    assert property_vector_store.find_top_candidates([0.0] * 384, k=5) == []


# --- Settings validation -----------------------------------------------------------


def test_field_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        FieldWeights(
            society_name=0.5,
            address=0.5,
            area_name=0.5,
            carpet_area_sqft=0.0,
            price=0.0,
            bhk=0.0,
            property_type=0.0,
        )


def test_numeric_thresholds_ordering_enforced():
    with pytest.raises(ValueError):
        NumericFieldThresholds(close_ratio=0.5, contradiction_ratio=0.2)


def test_duplicate_settings_score_ordering_enforced():
    with pytest.raises(ValueError):
        DuplicateDetectionSettings(high_confidence_score=0.4, low_confidence_score=0.6)
