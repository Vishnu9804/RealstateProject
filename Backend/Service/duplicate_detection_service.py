"""Duplicate-detection stage (Stage 5 — "Similarity Scoring & Threshold
Check" in the architecture diagram). Pure decision logic: given a new
property's embeddings, decides whether it's a duplicate of something
already accepted, genuinely new, or too uncertain to call automatically.
Does not store anything itself — storage is Service/property_vector_store.py;
property_pipeline_service.py wires the two together.

Architecture: retrieval vs. decision are deliberately separate steps.
  1. RETRIEVAL: the whole-property embedding (property_vector_store.
     find_top_candidates) narrows the field down to the top-K existing
     properties most likely to be related. This is the ONLY thing overall
     embedding similarity is used for.
  2. DECISION: each candidate is scored field-by-field — semantic
     similarity for free text (society/address/locality), relative
     numeric difference for price/area, exact-after-normalization for
     BHK/property type — combined into a weighted score. Raw embedding
     similarity never decides anything by itself.

Why: general-purpose sentence embeddings are trained on natural language
and are demonstrably weak at treating exact numbers as meaningfully
different — verified empirically during development, where changing only a
price or a phone number barely moved the overall similarity score at all.
Two listings that are 99%+ similar in wording can still be genuinely
different properties if the price, BHK, or area disagree outright. So a
disagreement in those fields is a hard contradiction that overrides the
weighted score entirely, in either direction of confidence.

Three outcomes, not two (see Model/duplicate_verdict.py): a wrong
HIGH_CONFIDENCE_DUPLICATE silently loses a real property; a wrong
HIGH_CONFIDENCE_NEW clutters the database with a redundant row. When the
evidence genuinely doesn't support either call with enough confidence, the
result is UNCERTAIN — stored, not discarded, and flagged for a human to
resolve (see property_pipeline_service.py).

None of this is claimed to be 100% accurate. WhatsApp listings are messy,
inconsistently worded, and often incomplete; the thresholds below are
reasoned starting points (see Model/duplicate_detection_settings.py's
docstrings for exactly which examples they were derived from), not proven
constants, and are expected to need recalibration against real data.

Contact phone is deliberately NOT used as evidence either way: in this
domain it's common and legitimate for the same listing to be re-posted by
several different brokers, each under their own number.
"""

from __future__ import annotations

import difflib
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from Database import settings_repository
from Database.session import is_database_configured
from Model.duplicate_check_result import DuplicateCheckResult
from Model.duplicate_detection_settings import DuplicateDetectionSettings, NumericFieldThresholds
from Model.duplicate_verdict import DuplicateVerdict
from Model.embedded_property import EmbeddedProperty
from Service import embedding_service, property_vector_store

_SETTINGS_KEY = "duplicate_detection_settings"

_settings = DuplicateDetectionSettings()


def load_from_database() -> None:
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _settings
        _settings = DuplicateDetectionSettings.model_validate(stored)


def get_settings() -> DuplicateDetectionSettings:
    return _settings


def set_settings(settings: DuplicateDetectionSettings) -> None:
    global _settings
    _settings = settings
    if is_database_configured():
        settings_repository.set_value(_SETTINGS_KEY, settings.model_dump())


def check_duplicate(new_property: EmbeddedProperty) -> DuplicateCheckResult:
    settings = _settings
    candidates = property_vector_store.find_top_candidates(new_property.embedding, settings.top_k_candidates)
    if not candidates:
        return DuplicateCheckResult(
            verdict=DuplicateVerdict.HIGH_CONFIDENCE_NEW,
            reason="No existing properties to compare against yet.",
        )

    best: Optional[DuplicateCheckResult] = None
    contradicted_count = 0
    all_contradictions: List[str] = []

    for candidate, _retrieval_similarity in candidates:
        weighted_score, evidence_ratio, field_scores, contradictions = _score_candidate(
            new_property, candidate, settings
        )
        if contradictions:
            contradicted_count += 1
            for reason_text in contradictions:
                if reason_text not in all_contradictions:
                    all_contradictions.append(reason_text)
            continue

        if best is None or weighted_score > best.weighted_score:
            best = DuplicateCheckResult(
                verdict=DuplicateVerdict.UNCERTAIN,  # placeholder — finalized once the best candidate is chosen
                weighted_score=weighted_score,
                evidence_ratio=evidence_ratio,
                matched_source_message_id=candidate.source_message_id,
                field_scores=field_scores,
                contradictions=[],
                reason="",
            )

    if best is None:
        return DuplicateCheckResult(
            verdict=DuplicateVerdict.HIGH_CONFIDENCE_NEW,
            contradictions=all_contradictions,
            reason=f"All {len(candidates)} retrieved candidate(s) had at least one conflicting field: "
            f"{'; '.join(all_contradictions)}.",
        )

    best.verdict, best.reason = _finalize_verdict(best, settings, contradicted_count, len(candidates))
    return best


def _finalize_verdict(
    result: DuplicateCheckResult, settings: DuplicateDetectionSettings, contradicted_count: int, candidate_count: int
) -> Tuple[DuplicateVerdict, str]:
    score = result.weighted_score
    evidence = result.evidence_ratio
    note = f" ({contradicted_count}/{candidate_count} other candidate(s) were ruled out by a contradiction.)" if contradicted_count else ""

    if score >= settings.high_confidence_score and evidence >= settings.min_evidence_ratio:
        return (
            DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE,
            f"Weighted field score {score:.3f} is at/above the high-confidence cutoff "
            f"({settings.high_confidence_score}), backed by {evidence:.0%} of total field weight.{note}",
        )

    if score < settings.low_confidence_score:
        return (
            DuplicateVerdict.HIGH_CONFIDENCE_NEW,
            f"Weighted field score {score:.3f} is below the low-confidence cutoff "
            f"({settings.low_confidence_score}).{note}",
        )

    if evidence < settings.min_evidence_ratio:
        return (
            DuplicateVerdict.UNCERTAIN,
            f"Weighted field score {score:.3f} looks high, but only {evidence:.0%} of total field weight was "
            f"backed by comparable data on both sides — not enough evidence to auto-confirm.{note}",
        )

    return (
        DuplicateVerdict.UNCERTAIN,
        f"Weighted field score {score:.3f} is between the low ({settings.low_confidence_score}) and high "
        f"({settings.high_confidence_score}) cutoffs — needs manual review.{note}",
    )


# --- field-level scoring ----------------------------------------------------


def _score_candidate(
    new_property: EmbeddedProperty, existing: EmbeddedProperty, settings: DuplicateDetectionSettings
) -> Tuple[float, float, Dict[str, Optional[float]], List[str]]:
    weights = settings.field_weights
    weight_map = {
        "society_name": weights.society_name,
        "address": weights.address,
        "area_name": weights.area_name,
        "carpet_area_sqft": weights.carpet_area_sqft,
        "price": weights.price,
        "bhk": weights.bhk,
        "property_type": weights.property_type,
    }
    field_scores: Dict[str, Optional[float]] = {}
    contradictions: List[str] = []

    for field_name in embedding_service.FIELD_EMBEDDING_NAMES:  # society_name, address, area_name
        score = _semantic_field_score(field_name, new_property, existing, settings.text_field_fuzzy_blend)
        field_scores[field_name] = score
        if score is not None and score < settings.semantic_contradiction_score:
            new_value = getattr(new_property, field_name)
            existing_value = getattr(existing, field_name)
            contradictions.append(f"{field_name.replace('_', ' ')} ({new_value!r} vs {existing_value!r})")

    area_score, area_conflict = _numeric_field_score(
        new_property.carpet_area_sqft, existing.carpet_area_sqft, settings.area_thresholds
    )
    field_scores["carpet_area_sqft"] = area_score
    if area_conflict:
        contradictions.append(f"carpet area ({new_property.carpet_area_sqft!r} vs {existing.carpet_area_sqft!r} sqft)")

    price_score, price_conflict = _price_field_score(new_property, existing, settings.price_thresholds)
    field_scores["price"] = price_score
    if price_conflict:
        contradictions.append(f"price ({new_property.price_text!r} vs {existing.price_text!r})")

    bhk_score, bhk_conflict = _categorical_field_score(new_property.bhk, existing.bhk, _normalize_categorical)
    field_scores["bhk"] = bhk_score
    if bhk_conflict:
        contradictions.append(f"BHK ({new_property.bhk!r} vs {existing.bhk!r})")

    type_score, type_conflict = _categorical_field_score(
        new_property.property_type, existing.property_type, _normalize_categorical
    )
    field_scores["property_type"] = type_score
    if type_conflict:
        contradictions.append(f"property type ({new_property.property_type!r} vs {existing.property_type!r})")

    comparable_weight = sum(weight_map[name] for name, score in field_scores.items() if score is not None)
    total_weight = sum(weight_map.values())
    evidence_ratio = (comparable_weight / total_weight) if total_weight else 0.0

    if comparable_weight == 0:
        weighted_score = 0.0
    else:
        weighted_score = (
            sum(weight_map[name] * score for name, score in field_scores.items() if score is not None)
            / comparable_weight
        )

    return weighted_score, evidence_ratio, field_scores, contradictions


def _semantic_field_score(
    field_name: str, new_property: EmbeddedProperty, existing: EmbeddedProperty, fuzzy_blend: float
) -> Optional[float]:
    new_value = getattr(new_property, field_name)
    existing_value = getattr(existing, field_name)
    if not new_value or not existing_value:
        return None  # absence is never a conflict — just not comparable

    norm_new = _normalize_semantic_text(new_value)
    norm_existing = _normalize_semantic_text(existing_value)
    if norm_new == norm_existing:
        return 1.0

    fuzzy = difflib.SequenceMatcher(None, norm_new, norm_existing).ratio()

    new_vec = new_property.field_embeddings.get(field_name)
    existing_vec = existing.field_embeddings.get(field_name)
    if new_vec is None or existing_vec is None:
        return fuzzy  # embeddings weren't cached for some reason — fuzzy-only fallback

    semantic = float(np.dot(np.array(new_vec), np.array(existing_vec)))
    score = (1 - fuzzy_blend) * semantic + fuzzy_blend * fuzzy
    return max(0.0, min(1.0, score))


def _numeric_field_score(
    a: Optional[float], b: Optional[float], thresholds: NumericFieldThresholds
) -> Tuple[Optional[float], bool]:
    """Returns (score in 0..1, is_contradiction). Relative difference is
    abs(a - b) / max(|a|, |b|) — symmetric, and scale-independent (a fixed
    rupee/sqft gap means something very different at different scales)."""
    if a is None or b is None:
        return None, False
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 1.0, False
    relative_diff = abs(a - b) / denom
    if relative_diff <= thresholds.close_ratio:
        return 1.0, False
    if relative_diff >= thresholds.contradiction_ratio:
        return 0.0, True
    span = thresholds.contradiction_ratio - thresholds.close_ratio
    return 1.0 - (relative_diff - thresholds.close_ratio) / span, False


def _price_field_score(
    new_property: EmbeddedProperty, existing: EmbeddedProperty, thresholds: NumericFieldThresholds
) -> Tuple[Optional[float], bool]:
    if new_property.price_amount_inr is not None and existing.price_amount_inr is not None:
        return _numeric_field_score(new_property.price_amount_inr, existing.price_amount_inr, thresholds)
    # Neither side gave a parsed numeric amount — fall back to comparing the
    # price text itself rather than treating it as unavailable.
    return _categorical_field_score(new_property.price_text, existing.price_text, _normalize_categorical)


def _categorical_field_score(
    a: Optional[str], b: Optional[str], normalizer: Callable[[Optional[str]], Optional[str]]
) -> Tuple[Optional[float], bool]:
    norm_a, norm_b = normalizer(a), normalizer(b)
    if norm_a is None or norm_b is None:
        return None, False
    if norm_a == norm_b:
        return 1.0, False
    return 0.0, True


def _normalize_categorical(value: Optional[str]) -> Optional[str]:
    """For fields compared exactly-after-normalization (BHK, property
    type): lowercase and strip ALL whitespace, so "2 BHK" == "2BHK"."""
    if not value:
        return None
    normalized = "".join(value.lower().split())
    return normalized or None


_SEMANTIC_PUNCTUATION = str.maketrans("", "", ",./-")


def _normalize_semantic_text(value: Optional[str]) -> Optional[str]:
    """For free-text fields (society/address/locality): lowercase, strip a
    small set of common punctuation, and collapse whitespace to single
    spaces — but keep word boundaries, unlike _normalize_categorical,
    since both the embedding model and the fuzzy matcher rely on them."""
    if not value:
        return None
    cleaned = value.lower().translate(_SEMANTIC_PUNCTUATION)
    normalized = " ".join(cleaned.split())
    return normalized or None
