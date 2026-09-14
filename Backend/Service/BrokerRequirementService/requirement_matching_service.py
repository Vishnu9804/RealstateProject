"""Matching for the DEMAND side: one broker requirement in, the same
high/medium/low matched properties out that a client inquiry already gets —
now STORED, like client matches are.

THE WHOLE POINT: IT IS THE SAME SCORING

A broker requirement and a client inquiry are the same thing said by two
different people — "someone wants a 3 BHK in Vesu under 90L". So this module
contains no scoring logic of its own. It adapts a StructuredRequirement into
the ClientRecord shape scoring.score_property already takes (see
_as_pseudo_client) and hands it to the existing, unmodified engine
(Service/ClientPropertyMatchingService/scoring.py): the same purpose
(buy/rent) and property-type critical gate, the same budget curve, location
and BHK scores, the same semantic comparison against the property's vector
built with the same free local embedding model (Service/
WhatsAppDataFetchingService/embedding_service.py) and the same canonical
requirement text (client_requirement_text_builder), the same cutoffs, the
same MatchBucket.

THE ONE RULE ADDED ON TOP: A BHK MEANS A HOME

A broker requirement very often names no property type ("2 BHK Fully
Furnished, Vesu"), and scoring treats a missing type as "every type is open".
A bedroom count is only ever asked of a home, so a requirement that asks for
a BHK but names no type never matches a land or commercial property (see
_excluded_for_requirement). Properties of unknown type are still scored.

HOW MATCHES ARE STORED AND KEPT CURRENT

Scores live in Postgres (Database/broker_requirement_match_models.py, via
requirement_match_store.py), so they exist for every requirement whether or
not anyone has opened it — the basis for match counts or alerts later.

  - A new requirement is scored the moment it is stored
    (score_new_requirements, called by requirement_pipeline_service) — one
    transaction for the whole batch.
  - Editing a requirement re-scores it in full (recompute_for_requirement),
    as does the dialog's Refresh.
  - Opening the dialog (get_matches_for_requirement) reads the stored rows
    and brings them current on the spot: if the requirement text changed
    since it was scored, a full re-score; otherwise ONLY the properties
    added or edited since then are scored (the property list is the
    in-memory snapshot, so this costs no database read), and rows for
    properties that no longer exist are dropped. It writes only when that
    catch-up actually had something to look at — reopening an unchanged
    requirement is one read and no write.

  - Every day at 6 AM IST (rescore_all_requirements, run by
    ClientPropertyMatchingService/scheduled_recompute_service after the
    client pass) every requirement is caught up the same incremental way,
    whether or not anyone opens it — so the Matches column and stored rows
    include the night's new properties. Properties a requirement was already
    scored against are never re-scored; a night with no new or edited
    property does one tiny read and no write.

Because every read catches up first, stored matches are never shown stale.

Deleting a requirement removes its stored matches through the foreign key's
ON DELETE CASCADE; marking a property sold out removes that property's rows
inside the sold-out transaction (Database/soldout_property_repository.py).

The requirement's own embedding is a local model call, memoised in memory
per requirement against the exact text it was built from (see
_requirement_vector) — it is never stored and never costs database traffic.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from Middleware import step_logger
from Model.BrokerRequirementModel.broker_requirement import StructuredRequirement
from Model.BrokerRequirementModel.requirement_match_result import RequirementMatchResult
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.BrokerRequirementService import requirement_match_store, requirement_store
from Service.ClientPropertyMatchingService import (
    client_requirement_text_builder,
    matching_service,
    normalization,
    scoring,
)
from Service.WhatsAppDataFetchingService import embedding_service, property_vector_store

# Same ceiling the client side scores under, and for the same reason — see
# matching_service's own comment on it. Read from there rather than
# re-declared, so the two can never be tuned apart.
_MAX_PROPERTIES_SCORED = matching_service._MAX_PROPERTIES_SCORED
# How many of the newest requirements the 6 AM catch-up covers — the most
# the Broker Requirements page can list (its controller caps limit at 1000).
_DAILY_REQUIREMENTS_WINDOW = 1000

# record_id -> (the exact text that was embedded, the resulting vector).
# Keyed on the TEXT, not just the id, so an edited requirement re-embeds
# instead of silently reusing the vector of what it used to say. Bounded for
# the same reason every other in-process cache here is.
_MAX_CACHED_VECTORS = 500
_vector_cache: Dict[str, Tuple[str, List[float]]] = {}


def get_matches_for_requirement(record_id: str) -> Optional[RequirementMatchResult]:
    """The dialog's read: stored matches, brought current first (see the
    module docstring). Returns None only when no requirement with this
    record_id exists (the controller turns that into a 404)."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None

    pseudo_client = _as_pseudo_client(requirement)
    if not matching_service.has_requirements(pseudo_client):
        return _build_result(requirement, pseudo_client, [], None, [])

    fingerprint = _fingerprint(pseudo_client)
    # Taken BEFORE the property list is read — see
    # BrokerRequirementMatchRunRow.computed_at.
    run_started_at = _now()
    properties = property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
    scores, computed_at, stored_fingerprint = requirement_match_store.get_matches(record_id)

    if computed_at is None or stored_fingerprint != fingerprint:
        # Never scored, or the requirement changed since: every stored score
        # is suspect, so a full re-score replaces them.
        scores = _score(requirement, pseudo_client, properties)
        requirement_match_store.replace_matches({record_id: (scores, fingerprint)}, run_started_at)
        computed_at = run_started_at
    else:
        changed = property_vector_store.get_properties_changed_since(computed_at, limit=_MAX_PROPERTIES_SCORED)
        live_ids = {prop.record_id for prop in properties}
        gone = {score.record_id for score in scores if score.record_id not in live_ids}
        if changed or gone:
            rescored = _score(requirement, pseudo_client, changed)
            considered = {prop.record_id for prop in changed} | gone
            requirement_match_store.merge_matches(record_id, rescored, considered, run_started_at, fingerprint)
            scores = [score for score in scores if score.record_id not in considered] + rescored
            computed_at = run_started_at

    result = _build_result(requirement, pseudo_client, scores, computed_at, properties)
    step_logger.info(
        f"[Matching] requirement {record_id}: {len(result.high)} high, {len(result.medium)} medium, "
        f"{len(result.low)} low."
    )
    return result


def get_match_counts(limit: int = 500) -> Dict[str, int]:
    """record_id -> how many properties its matches dialog lists, for the
    Broker Requirements table's Matches column. A read only: nothing is
    scored and nothing is written, so it is safe to ask whenever the list
    loads. The live property set comes from the in-memory snapshot (no
    query), and the counts are one aggregate query (see
    broker_requirement_match_repository.get_match_counts).

    Counted against the same matchable, still-existing properties
    _build_result shows, so the number equals the dialog's own total for
    what is stored. Properties that arrived after a requirement was last
    scored are picked up when its dialog is opened (the catch-up in
    get_matches_for_requirement), and the page then updates that one row's
    count from the dialog's result. A never-scored requirement is absent."""
    live_ids = {
        prop.record_id
        for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
        if matching_service.is_matchable(prop)
    }
    return requirement_match_store.get_match_counts(live_ids, limit)


def recompute_for_requirement(record_id: str) -> Optional[RequirementMatchResult]:
    """Full re-score of one requirement, replacing whatever was stored —
    the dialog's Refresh, and what an edit to the requirement runs. None when
    no such requirement exists."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None
    pseudo_client = _as_pseudo_client(requirement)
    run_started_at = _now()
    properties = property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
    scores = (
        _score(requirement, pseudo_client, properties) if matching_service.has_requirements(pseudo_client) else []
    )
    requirement_match_store.replace_matches(
        {record_id: (scores, _fingerprint(pseudo_client))}, run_started_at
    )
    return _build_result(requirement, pseudo_client, scores, run_started_at, properties)


def score_new_requirements(requirements: List[StructuredRequirement]) -> int:
    """Scores a freshly stored batch of requirements and stores every result
    in ONE write (see broker_requirement_match_repository.replace_matches).
    The property list is read once, from memory, for the whole batch.
    Returns how many matches were stored."""
    if not requirements:
        return 0
    run_started_at = _now()
    properties = property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
    results: Dict[str, Tuple[List[MatchScore], str]] = {}
    for requirement in requirements:
        pseudo_client = _as_pseudo_client(requirement)
        scores = (
            _score(requirement, pseudo_client, properties)
            if matching_service.has_requirements(pseudo_client)
            else []
        )
        results[requirement.record_id] = (scores, _fingerprint(pseudo_client))
    return requirement_match_store.replace_matches(results, run_started_at)


def rescore_all_requirements() -> Tuple[int, int, int]:
    """The 6 AM catch-up: every requirement (newest _DAILY_REQUIREMENTS_WINDOW)
    scored against ONLY the properties added or edited since it was last
    scored. Returns (requirements rescored, already up to date, match rows
    written).

    Database cost, in order:
      1. one query for every requirement's watermark (id + timestamp + hash,
         see broker_requirement_match_repository.get_run_index);
      2. the changed-property lists come from the in-memory snapshot — no
         query — memoised per watermark, since most requirements share one;
      3. only if something changed: one query loading just those requirements;
      4. one transaction writing all of their results (plus a full-replace
         transaction in the rare case a requirement was never scored or its
         text changed without a re-score).
    A requirement with nothing new is not loaded and not written."""
    # Taken BEFORE any property list is read — see
    # BrokerRequirementMatchRunRow.computed_at.
    run_started_at = _now()
    runs = requirement_match_store.get_run_index(_DAILY_REQUIREMENTS_WINDOW)
    changed_by_watermark: Dict[Optional[datetime], List[EmbeddedProperty]] = {}
    pending: Dict[str, Tuple[Optional[datetime], Optional[str], List[EmbeddedProperty]]] = {}
    for record_id, (computed_at, fingerprint) in runs.items():
        if computed_at not in changed_by_watermark:
            changed_by_watermark[computed_at] = property_vector_store.get_properties_changed_since(
                computed_at, limit=_MAX_PROPERTIES_SCORED
            )
        if changed_by_watermark[computed_at]:
            pending[record_id] = (computed_at, fingerprint, changed_by_watermark[computed_at])
    if not pending:
        return 0, len(runs), 0

    all_properties: Optional[List[EmbeddedProperty]] = None
    full: Dict[str, Tuple[List[MatchScore], str]] = {}
    incremental: Dict[str, Tuple[List[MatchScore], set, str]] = {}
    for requirement in requirement_store.get_requirements_by_record_ids(list(pending)):
        computed_at, stored_fingerprint, changed = pending[requirement.record_id]
        try:
            pseudo_client = _as_pseudo_client(requirement)
            fingerprint = _fingerprint(pseudo_client)
            has_requirements = matching_service.has_requirements(pseudo_client)
            if computed_at is None or stored_fingerprint != fingerprint:
                # Never scored, or its text changed since: nothing stored can
                # be trusted, so this one alone gets a full re-score.
                if all_properties is None:
                    all_properties = property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
                scores = _score(requirement, pseudo_client, all_properties) if has_requirements else []
                full[requirement.record_id] = (scores, fingerprint)
            else:
                scores = _score(requirement, pseudo_client, changed) if has_requirements else []
                incremental[requirement.record_id] = (scores, {prop.record_id for prop in changed}, fingerprint)
        except Exception as exc:  # noqa: BLE001
            # One bad requirement must not skip the rest. Not written, so its
            # watermark stays put and the next run retries it from there.
            step_logger.error(
                f"[Daily Matching] Rescore failed for requirement {requirement.record_id} "
                f"({type(exc).__name__}): {exc!r}"
            )

    written = requirement_match_store.replace_matches(full, run_started_at) if full else 0
    written += requirement_match_store.merge_matches_bulk(incremental, run_started_at)
    return len(full) + len(incremental), len(runs) - len(pending), written


def forget_requirement(record_id: str) -> None:
    """Called when a requirement is deleted: drops its memoised vector, and
    (in-memory fallback only) its stored matches — with a database the
    foreign key has already cascaded them away."""
    _vector_cache.pop(record_id, None)
    requirement_match_store.forget_requirement_in_memory(record_id)


def drop_property_from_memory_cache(property_record_id: str) -> None:
    """In-memory fallback's counterpart of the sold-out transaction deleting
    this property's broker match rows."""
    requirement_match_store.drop_property_in_memory(property_record_id)


def _score(
    requirement: StructuredRequirement, pseudo_client: ClientRecord, properties: Iterable[EmbeddedProperty]
) -> List[MatchScore]:
    """Every matchable property in `properties` that clears scoring's
    cutoff, at most one score per property (the stored rows are unique per
    property). The requirement is only embedded when there is actually
    something to score."""
    candidates = [prop for prop in properties if matching_service.is_matchable(prop)]
    if not candidates:
        return []
    vector = _requirement_vector(requirement, pseudo_client)
    bhk_without_type = _asks_bhk_without_type(requirement)
    best: Dict[str, MatchScore] = {}
    for prop in candidates:
        if _excluded_for_requirement(bhk_without_type, prop):
            continue
        score = scoring.score_property(pseudo_client, prop, vector)
        if score is None:
            continue
        previous = best.get(score.record_id)
        if previous is None or score.score > previous.score:
            best[score.record_id] = score
    return list(best.values())


def _fingerprint(pseudo_client: ClientRecord) -> str:
    """sha256 of the exact text the requirement is scored and embedded from.
    Every field scoring reads — buy/rent, type, BHK, budget, areas, society,
    description — is part of that text, so any change to them changes this."""
    text = client_requirement_text_builder.build_requirement_text(pseudo_client)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _asks_bhk_without_type(requirement: StructuredRequirement) -> bool:
    return not (requirement.requirement_type or "").strip() and bool((requirement.bhk or "").strip())


def _excluded_for_requirement(bhk_without_type: bool, prop: EmbeddedProperty) -> bool:
    """See the module docstring's "A BHK MEANS A HOME". Only ever removes a
    land/commercial property from a requirement that asked for a BHK and
    named no type; every other pair is left entirely to scoring."""
    return bhk_without_type and normalization.is_non_residential_type(prop.property_type)


def _as_pseudo_client(requirement: StructuredRequirement) -> ClientRecord:
    """The adapter this whole module exists for: a StructuredRequirement
    expressed in the ClientRecord fields scoring.py reads.

    Field by field, and why:

      - purpose: derived from listing_type. A requirement says "Sale" or
        "Rent" (what the broker wants to do); a client says "buy" or "rent".
        scoring's purpose gate speaks the client's vocabulary, so the
        translation happens here and the gate stays untouched.
      - property_type: requirement_type — written with the same names
        StructuredProperty.property_type uses, which is exactly what
        normalization.property_type_gate compares against. Several types
        arrive comma-separated main-first, which is the shape that gate
        already splits on ("Flat, Row House").
      - preferred_areas: the full list, comma-joined, because scoring's
        location score splits a client's own free-text field on commas and
        slashes. area_name is only the first of that list (see
        StructuredRequirement's own comment), so it is a fallback for the
        empty-list case, never a replacement.
      - additional_requirements: the free text a requirement carries that
        has no dedicated field on ClientRecord — the society asked for by
        name and the description, which holds every other stated detail
        (furnishing, size, who it is for, food, possession, ...). It feeds
        the semantic half of the score (see
        client_requirement_text_builder.build_requirement_text), which is
        where an unstructured "veg family, fully furnished" belongs.
      - a wanted size is not scored — the same as for a client, who has
        nowhere to state one either — which is why a requirement no longer
        stores it as numbers at all; it stays readable in the description.

    `phone` is required by the model and is filled with the requirement's
    own sender number. Nothing reads it on this path (no cache is keyed by
    it, no row is written for it) — it is here so the object is valid, and
    it is the truthful value rather than a placeholder.
    """
    areas = [area for area in (requirement.preferred_areas or []) if area and area.strip()]
    if not areas and requirement.area_name:
        areas = [requirement.area_name]
    extra = [requirement.society_name, requirement.description]
    return ClientRecord(
        phone=requirement.sender_phone,
        name=requirement.contact_name or requirement.sender_saved_name or requirement.sender_name,
        purpose="rent" if requirement.listing_type == "Rent" else "buy",
        property_type=requirement.requirement_type,
        bhk=requirement.bhk,
        budget_min_inr=requirement.budget_min_inr,
        budget_max_inr=requirement.budget_max_inr,
        preferred_areas=", ".join(areas) if areas else None,
        additional_requirements=" | ".join(part.strip() for part in extra if part and part.strip()) or None,
    )


def _requirement_vector(requirement: StructuredRequirement, pseudo_client: ClientRecord) -> List[float]:
    """The requirement's embedding, built from exactly the same canonical
    text a client's is (client_requirement_text_builder) with exactly the
    same model a client's and every property's is (embedding_service) — so
    the two land in the same semantic space as the property vectors they are
    compared against. That shared construction, not merely the shared model,
    is what makes the cosine similarity in scoring's semantic component mean
    anything.

    Memoised as record_id -> (text, vector): a cache hit requires the text to
    be identical, so an edit re-embeds and an unrelated property arriving
    does not."""
    text = client_requirement_text_builder.build_requirement_text(pseudo_client)
    if not text:
        return []
    cached = _vector_cache.get(requirement.record_id)
    if cached is not None and cached[0] == text:
        return cached[1]
    vector = embedding_service.embed_text(text)
    if len(_vector_cache) >= _MAX_CACHED_VECTORS:
        _vector_cache.pop(next(iter(_vector_cache)), None)
    _vector_cache[requirement.record_id] = (text, vector)
    return vector


def _build_result(
    requirement: StructuredRequirement,
    pseudo_client: ClientRecord,
    scores: List[MatchScore],
    computed_at: Optional[datetime],
    properties: List[EmbeddedProperty],
) -> RequirementMatchResult:
    """Joins each score against the property's CURRENT display fields, from
    the same in-memory property list the scores were brought current
    against — a property edited or moved between Main/Outsider since it was
    scored shows its live values, and one that no longer exists is skipped."""
    properties_by_id = {prop.record_id: prop for prop in properties if matching_service.is_matchable(prop)}
    high: List[MatchedProperty] = []
    medium: List[MatchedProperty] = []
    low: List[MatchedProperty] = []
    buckets = {MatchBucket.HIGH: high, MatchBucket.MEDIUM: medium, MatchBucket.LOW: low}

    for match in scores:
        prop = properties_by_id.get(match.record_id)
        if prop is None:
            continue
        buckets[match.bucket].append(
            MatchedProperty(**match.model_dump(), **matching_service.display_fields(prop))
        )

    for group in (high, medium, low):
        group.sort(key=lambda m: m.score, reverse=True)

    return RequirementMatchResult(
        record_id=requirement.record_id,
        requirement_summary=_summarize(requirement),
        has_requirements=matching_service.has_requirements(pseudo_client),
        computed_at=computed_at,
        high=high,
        medium=medium,
        low=low,
    )


def _summarize(requirement: StructuredRequirement) -> str:
    areas = [area for area in (requirement.preferred_areas or []) if area and area.strip()]
    if not areas and requirement.area_name:
        areas = [requirement.area_name]
    parts = [
        " ".join(part for part in (requirement.bhk, requirement.requirement_type) if part) or None,
        "to rent" if requirement.listing_type == "Rent" else "to buy",
        ", ".join(areas) if areas else None,
    ]
    return " · ".join(part for part in parts if part)
