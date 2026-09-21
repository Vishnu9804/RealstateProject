"""The deterministic area work that happens AFTER the LLM has structured a
property, and the per-type size a broker requirement now carries.

Both are arithmetic and string handling with no model call in them, which is
exactly why they are worth pinning: the extraction prompt asks for the same
things (see property_structurer._build_system_prompt's AREA sections), and
these are the guarantees that hold whatever the model happens to return.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from Agent.WhatsAppDataFetchingAgent import property_structurer
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Service.BrokerRequirementService.requirement_pipeline_service import _align_property_sizes
from Service.ClientPropertyMatchingService import normalization


def _property(**overrides) -> StructuredProperty:
    fields = dict(
        source_message_id="m1",
        group_name="Brokers",
        chat_type="group",
        sender_name="Ramesh",
        sender_saved_name="Ramesh",
        sender_phone="+919000000000",
        message_text="",
        message_timestamp=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return StructuredProperty(**fields)


class FillMissingAreaUnitTests(unittest.TestCase):
    """A vaar is exactly nine square feet, so a listing known in one unit is
    known in both. Filling the empty column is what puts it back into the
    Area (var) filter and sort it used to fall out of."""

    def test_vaar_is_derived_from_sqft(self):
        prop = _property(area_sqft=1200)
        property_structurer._fill_missing_area_unit(prop)
        self.assertEqual(prop.area_sqft, 1200)
        self.assertAlmostEqual(prop.area_vaar, 133.33, places=2)

    def test_sqft_is_derived_from_vaar(self):
        prop = _property(area_vaar=94)
        property_structurer._fill_missing_area_unit(prop)
        self.assertEqual(prop.area_sqft, 846)
        self.assertEqual(prop.area_vaar, 94)

    def test_a_message_quoting_both_is_left_exactly_as_it_quoted_them(self):
        # The broker's own two figures. They may disagree slightly (rounding,
        # carpet vs built-up) and that disagreement is information, not an
        # error to be "corrected" into a clean x9.
        prop = _property(area_sqft=850, area_vaar=94)
        property_structurer._fill_missing_area_unit(prop)
        self.assertEqual((prop.area_sqft, prop.area_vaar), (850, 94))

    def test_no_area_at_all_stays_no_area(self):
        prop = _property()
        property_structurer._fill_missing_area_unit(prop)
        self.assertIsNone(prop.area_sqft)
        self.assertIsNone(prop.area_vaar)

    def test_a_zero_is_not_a_size_and_derives_nothing(self):
        prop = _property(area_sqft=0)
        property_structurer._fill_missing_area_unit(prop)
        self.assertIsNone(prop.area_vaar)


class DimensionRecoveryTests(unittest.TestCase):
    """"18x40" is a length and a breadth in feet. The prompt asks the model
    for the product directly; this is the safety net under that, and it only
    ever speaks when the model said nothing at all."""

    def test_dimensions_become_square_feet(self):
        prop = _property(message_text="Plot 18x40 in Vesu, 45L")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertEqual(prop.area_sqft, 720)

    def test_a_decimal_side_is_ordinary(self):
        prop = _property(message_text="20x42.3 plot for sale")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertEqual(prop.area_sqft, 846)

    def test_an_area_the_model_already_gave_is_never_second_guessed(self):
        prop = _property(message_text="1200 sqft flat, 18x40 terrace", area_sqft=1200)
        property_structurer._recover_area_from_dimensions(prop)
        self.assertEqual(prop.area_sqft, 1200)

    def test_a_range_is_not_a_multiplication(self):
        prop = _property(message_text="70, 80 vaar plot")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertIsNone(prop.area_sqft)

    def test_a_bedroom_count_is_not_a_dimension(self):
        prop = _property(message_text="Nice 12x3 BHK block")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertIsNone(prop.area_sqft)

    def test_two_different_dimensions_are_ambiguous_and_are_skipped(self):
        prop = _property(message_text="Two plots: 18x40 and 25x50")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertIsNone(prop.area_sqft)

    def test_the_same_dimension_written_twice_is_still_one_plot(self):
        prop = _property(message_text="18x40 plot. Size 18 x 40.")
        property_structurer._recover_area_from_dimensions(prop)
        self.assertEqual(prop.area_sqft, 720)


class StoredSizeTextTests(unittest.TestCase):
    """The forms store a size as the number (or range) a person typed with
    the unit they picked appended — "1000-1500 sqft", "150 var". These are
    read by the matcher's existing parser with no change to it, which is the
    whole reason that shape was chosen."""

    def test_a_range_in_sqft(self):
        self.assertEqual(normalization.parse_size_requirement("1000-1500 sqft", "Flat"), (1000.0, 1500.0))

    def test_the_word_to_is_a_range_too(self):
        self.assertEqual(normalization.parse_size_requirement("70 to 80 sqft", "Shop"), (70.0, 80.0))

    def test_var_is_read_as_vaar(self):
        low, high = normalization.parse_size_requirement("70 - 80 var", "Plot")
        self.assertEqual((low, high), (630.0, 720.0))

    def test_a_unit_written_in_the_text_beats_the_type_s_usual_one(self):
        # A bungalow's size used to be assumed to be in vaar. Quoted in sqft,
        # it must be read as sqft — this is the bug the unit capsule exists
        # to stop.
        low, high = normalization.parse_size_requirement("1200 sqft", "Bungalow")
        self.assertAlmostEqual(low, 1080.0)
        self.assertAlmostEqual(high, 1320.0)


class AlignRequirementSizesTests(unittest.TestCase):
    """A broker requirement's sizes are looked up BY their type name later,
    so they have to be keyed by the names actually stored."""

    def test_sizes_are_rekeyed_onto_the_canonical_type_names(self):
        # "Apartment" is stored as "Flat" (canonical_requirement_type), so a
        # size keyed "Apartment" has to follow it.
        self.assertEqual(
            _align_property_sizes({"Apartment": "1000-1500 sqft", "Plot": "150 var"}, "Flat, Plot"),
            {"Flat": "1000-1500 sqft", "Plot": "150 var"},
        )

    def test_a_size_for_an_unticked_type_is_dropped(self):
        self.assertIsNone(_align_property_sizes({"Shop": "600 sqft"}, "Flat"))

    def test_blank_sizes_are_not_preferences(self):
        self.assertIsNone(_align_property_sizes({"Flat": "   "}, "Flat"))

    def test_nothing_in_nothing_out(self):
        self.assertIsNone(_align_property_sizes(None, "Flat"))
        self.assertIsNone(_align_property_sizes({"Flat": "1200 sqft"}, None))

    def test_the_order_follows_the_type_list(self):
        aligned = _align_property_sizes({"Plot": "150 var", "Flat": "1200 sqft"}, "Flat, Plot")
        self.assertEqual(list(aligned), ["Flat", "Plot"])

    def test_a_size_found_by_the_matcher_by_its_type(self):
        aligned = _align_property_sizes({"Apartment": "1200 sqft"}, "Flat")
        self.assertEqual(normalization.size_for(aligned, "Flat"), "1200 sqft")


if __name__ == "__main__":
    unittest.main()
