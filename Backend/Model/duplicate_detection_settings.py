from pydantic import BaseModel, model_validator


class FieldWeights(BaseModel):
    """How much each field contributes to the overall weighted duplicate
    score, when that field is actually comparable (present on both sides).
    Must sum to 1.0.

    These are INITIAL, reasoned defaults — not calibrated against real
    client data (none exists yet). society_name carries the most weight
    because a named building/project is the single most specific
    identifier a WhatsApp listing typically gives; property_type carries
    the least because it is coarse (e.g. "Flat" vs "Apartment" may just be
    wording, not a real distinction) and mostly useful as a contradiction
    check (see Service/duplicate_detection_service.py) rather than a
    fine-grained similarity signal.
    """

    society_name: float = 0.25
    address: float = 0.15
    area_name: float = 0.10
    carpet_area_sqft: float = 0.10
    price: float = 0.20
    bhk: float = 0.15
    property_type: float = 0.05

    @model_validator(mode="after")
    def _check_sum(self) -> "FieldWeights":
        total = (
            self.society_name
            + self.address
            + self.area_name
            + self.carpet_area_sqft
            + self.price
            + self.bhk
            + self.property_type
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Field weights must sum to 1.0 (got {total}).")
        return self


class NumericFieldThresholds(BaseModel):
    """Relative-difference cutoffs for a numeric field (price or carpet
    area), as a fraction of the larger of the two values:
    relative_difference = abs(a - b) / max(a, b).

    - relative_difference <= close_ratio: field score is 1.0 (as close as
      makes no difference).
    - relative_difference >= contradiction_ratio: field score is 0.0 AND
      this field is flagged as a hard contradiction (overrides the
      weighted score entirely — see check_duplicate).
    - in between: the score decreases linearly from 1.0 to 0.0, and the
      pair is NOT treated as a contradiction — it's ordinary evidence fed
      into the weighted average.

    NOT calibrated against real client data — derived from the worked
    examples in the redesign request (e.g. 45L->48L should land in the
    uncertain/corroboration zone, 45L->55L should land in contradiction;
    1200->1300 sqft uncertain, 1200->1800 sqft contradiction). Needs
    recalibration once real historical listings are available.
    """

    close_ratio: float
    contradiction_ratio: float

    @model_validator(mode="after")
    def _check_ordering(self) -> "NumericFieldThresholds":
        if not (0.0 <= self.close_ratio < self.contradiction_ratio <= 1.0):
            raise ValueError("Must satisfy 0 <= close_ratio < contradiction_ratio <= 1.")
        return self


class DuplicateDetectionSettings(BaseModel):
    field_weights: FieldWeights = FieldWeights()

    # Price fluctuates legitimately (re-listings, negotiation) more than
    # floor area does, so its contradiction cutoff is deliberately looser.
    price_thresholds: NumericFieldThresholds = NumericFieldThresholds(close_ratio=0.05, contradiction_ratio=0.15)
    area_thresholds: NumericFieldThresholds = NumericFieldThresholds(close_ratio=0.02, contradiction_ratio=0.20)

    # How many existing properties (ranked by whole-property embedding
    # similarity) get a full field-level comparison. Embeddings only ever
    # narrow the candidate list — see Service/property_vector_store.py.
    top_k_candidates: int = 10

    # A weighted score can only be labeled HIGH_CONFIDENCE_DUPLICATE if at
    # least this fraction of the total field weight was actually backed by
    # comparable data on both sides — otherwise a single lucky field match
    # could produce a deceptively high score from almost no real evidence.
    min_evidence_ratio: float = 0.5

    # Weighted-score cutoffs for the final verdict (evaluated only once no
    # field has triggered a hard contradiction).
    high_confidence_score: float = 0.85
    low_confidence_score: float = 0.55

    # For semantic (free-text) fields: how much weight the fuzzy
    # character-level signal gets versus the embedding-based semantic
    # signal. 0.0 = embedding only, 1.0 = fuzzy-string only.
    text_field_fuzzy_blend: float = 0.3

    # A semantic field score (society_name/address/area_name) below this is
    # treated as a hard contradiction, the same as a numeric/categorical
    # one — a low weight alone (e.g. area_name's 10%) isn't reliably enough
    # to stop a clearly-different locality from being outvoted by five
    # fields that all happen to agree. Deliberately conservative (well
    # below where a spelling variation or abbreviation would score) so it
    # only fires on text that's genuinely unrelated.
    semantic_contradiction_score: float = 0.3

    @model_validator(mode="after")
    def _check_ordering(self) -> "DuplicateDetectionSettings":
        if not (0.0 <= self.low_confidence_score <= self.high_confidence_score <= 1.0):
            raise ValueError("Must satisfy 0 <= low_confidence_score <= high_confidence_score <= 1.")
        if not (0.0 <= self.min_evidence_ratio <= 1.0):
            raise ValueError("min_evidence_ratio must be between 0 and 1.")
        if not (0.0 <= self.text_field_fuzzy_blend <= 1.0):
            raise ValueError("text_field_fuzzy_blend must be between 0 and 1.")
        if not (0.0 <= self.semantic_contradiction_score <= 1.0):
            raise ValueError("semantic_contradiction_score must be between 0 and 1.")
        if self.top_k_candidates < 1:
            raise ValueError("top_k_candidates must be at least 1.")
        return self
