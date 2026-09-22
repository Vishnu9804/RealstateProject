"""Matching for the DEMAND side: one broker requirement in, the same
high/medium/low matched properties out that a client inquiry already gets —
now STORED, like client matches are.

"Properties" here means every match candidate: the stored properties AND
the Builder Projects page's projects, gathered in one place for both sides
(Service/ClientPropertyMatchingService/match_candidates.py) and scored by
the same engine. Wherever this module talks about "the property list", it
means that combined list.

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

SEVERAL ACCEPTABLE TYPES ARE SCORED SEPARATELY

A requirement very often names more than one acceptable type ("2/3 BHK full
furnished, row house chale" -> "Flat, Row House"). Each of them is an equal
preference, so — exactly as for a client who ticked several types on the
requirements form — the property is scored once per type and keeps its best
result, tagged with the type it was for. That tag (MatchScore.matched_type)
is what the matches dialog's Property type row splits on, and it is produced
by scoring.score_client_property, the same function the client side uses. A
requirement with one type, or none, goes through scoring.score_property
exactly as it always did.

NO RULES OF ITS OWN, NOT EVEN ONE

This module used to add a single rule on top of the shared engine: a bedroom
count means a home, so a requirement asking for a BHK with no type named
never matched a plot or a shop. That rule was right — and the client side
never had it, so a client who asked for "3 BHK" and named no type WAS being
shown plots. It now lives in the engine's own eligibility gate
(scoring.is_eligible, config.REJECT_NON_RESIDENTIAL_FOR_BHK) where both
surfaces get it, and this module is left with no scoring logic whatsoever.

THE SAME CEILING, TOO

A requirement keeps at most MAX_MATCHES_PER_REQUIREMENT matches, chosen by
the same ranking a client's shortlist uses (see best_matches below). That was
the one thing the demand side genuinely lacked.

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
_requirement_vector), and persisted to BrokerRequirementRow.embedding
whenever a fresh one is computed — one small write, only on a cache miss
(a new requirement, an edited one, or the first score after a process
restart), never on every read.
"""

from __future__ import annotations

import hashlib
import heapq
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
    match_candidates,
    match_config,
    matching_service,
    normalization,
    scoring,
)
from Service.WhatsAppDataFetchingService import embedding_service

# The listings scored, and how many of each, come from match_candidates —
# the same source and the same ceilings the client side scores under, so
# the two can never be tuned apart.
# How many of the newest requirements the 6 AM catch-up covers — the most
# the Broker Requirements page can list (its controller caps limit at 1000).
_DAILY_REQUIREMENTS_WINDOW = 1000

# record_id -> (the exact text that was embedded, the resulting vector).
# Keyed on the TEXT, not just the id, so an edited requirement re-embeds
# instead of silently reusing the vector of what it used to say. Bounded for
# the same reason every other in-process cache here is. Kept small on
# purpose: a miss just re-embeds one short string (cheap), so this cache
# only needs to cover a dialog being reopened in quick succession, not every
# requirement ever scored.
_MAX_CACHED_VECTORS = 150
_vector_cache: Dict[str, Tuple[str, List[float]]] = {}


def get_matches_for_requirement(record_id: str) -> Optional[RequirementMatchResult]:
    """The dialog's read: stored matches, brought current first (see the
    module docstring). Returns None only when no requirement with this
    record_id exists (the controller turns that into a 404)."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None

    pseudo_client = _as_pseudo_client(requirement)
    if not _has_criteria(pseudo_client):
        # Nothing to compare properties against — and nothing may be left
        # STORED from before this was checked either, or the table's Matches
        # column (get_match_counts, which counts stored rows) would keep
        # advertising the hundred meaningless matches this requirement used
        # to collect while the dialog correctly showed none. One read, and a
        # write only on the first open that finds something to clear; after
        # that this is the read alone.
        stored, _, _ = requirement_match_store.get_matches(record_id)
        if stored:
            requirement_match_store.replace_matches({record_id: ([], _fingerprint(pseudo_client))}, _now())
        return _build_result(requirement, pseudo_client, [], None, [])

    fingerprint = _fingerprint(pseudo_client)
    # Taken BEFORE the property list is read — see
    # BrokerRequirementMatchRunRow.computed_at.
    run_started_at = _now()
    properties = match_candidates.get_all()
    scores, computed_at, stored_fingerprint = requirement_match_store.get_matches(record_id)

    if computed_at is None or stored_fingerprint != fingerprint:
        # Never scored, or the requirement changed since: every stored score
        # is suspect, so a full re-score replaces them.
        scores = _score(requirement, pseudo_client, properties)
        requirement_match_store.replace_matches({record_id: (scores, fingerprint)}, run_started_at)
        computed_at = run_started_at
    else:
        changed = match_candidates.get_changed_since(computed_at)
        live_ids = {prop.record_id for prop in properties}
        gone = {score.record_id for score in scores if score.record_id not in live_ids}
        if changed or gone:
            rescored = _score(requirement, pseudo_client, changed)
            considered = {prop.record_id for prop in changed} | gone
            requirement_match_store.merge_matches(
                record_id,
                rescored,
                considered,
                run_started_at,
                fingerprint,
                keep_best=match_config.MAX_MATCHES_PER_REQUIREMENT,
            )
            # Trimmed the same way the store just trimmed the stored rows, so
            # the dialog shows exactly what is held and not a longer list that
            # would shrink on the next open.
            scores = best_matches(
                [score for score in scores if score.record_id not in considered] + rescored
            )
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
    # Ids only — nothing is scored, so no builder project's vector is needed.
    live_ids = {
        prop.record_id
        for prop in match_candidates.get_all(ensure_embeddings=False)
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
    properties = match_candidates.get_all()
    scores = (
        _score(requirement, pseudo_client, properties) if _has_criteria(pseudo_client) else []
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
    properties = match_candidates.get_all()
    results: Dict[str, Tuple[List[MatchScore], str]] = {}
    for requirement in requirements:
        pseudo_client = _as_pseudo_client(requirement)
        scores = (
            _score(requirement, pseudo_client, properties)
            if _has_criteria(pseudo_client)
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
            changed_by_watermark[computed_at] = match_candidates.get_changed_since(computed_at)
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
            has_requirements = _has_criteria(pseudo_client)
            if computed_at is None or stored_fingerprint != fingerprint:
                # Never scored, or its text changed since: nothing stored can
                # be trusted, so this one alone gets a full re-score.
                if all_properties is None:
                    all_properties = match_candidates.get_all()
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
    written += requirement_match_store.merge_matches_bulk(
        incremental, run_started_at, keep_best=match_config.MAX_MATCHES_PER_REQUIREMENT
    )
    return len(full) + len(incremental), len(runs) - len(pending), written


def score_builder_projects_for_scored_requirements() -> Tuple[int, int, int]:
    """The one-time pass that brings builder projects into matches that were
    stored BEFORE builder projects were matched (see
    ClientPropertyMatchingService/scheduled_recompute_service.
    start_builder_project_introduction_in_background). Returns (requirements
    updated, match rows written, requirements that failed).

    Why it is needed at all: every later read catches a requirement up only
    on listings changed since its watermark, and a project saved before then
    is older than that watermark — without this, it would reach existing
    requirements only through a full re-score.

    What it deliberately does NOT do is move any watermark (see
    requirement_match_store.merge_matches_bulk's keep_watermarks): only the
    builder projects are looked at here, so a property changed since a
    requirement was last scored must still be caught up by that
    requirement's next read, exactly as before.

    Skipped, because their next read already re-scores them in full with
    builder projects included: requirements never scored, and requirements
    whose text changed since they were scored."""
    candidates = match_candidates.get_builder_projects()
    if not candidates:
        return 0, 0, 0
    runs = requirement_match_store.get_run_index(_DAILY_REQUIREMENTS_WINDOW)
    scored_ids = [record_id for record_id, (computed_at, _) in runs.items() if computed_at is not None]
    if not scored_ids:
        return 0, 0, 0
    considered = {candidate.record_id for candidate in candidates}
    updates: Dict[str, Tuple[List[MatchScore], set, str]] = {}
    failed = 0
    for requirement in requirement_store.get_requirements_by_record_ids(scored_ids):
        try:
            pseudo_client = _as_pseudo_client(requirement)
            fingerprint = _fingerprint(pseudo_client)
            if fingerprint != runs[requirement.record_id][1]:
                continue
            scores = (
                _score(requirement, pseudo_client, candidates) if _has_criteria(pseudo_client) else []
            )
            updates[requirement.record_id] = (scores, considered, fingerprint)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            step_logger.error(
                f"[Matching] Builder projects could not be scored for requirement {requirement.record_id} "
                f"({type(exc).__name__}): {exc!r}"
            )
    written = (
        requirement_match_store.merge_matches_bulk(
            updates, _now(), keep_watermarks=True, keep_best=match_config.MAX_MATCHES_PER_REQUIREMENT
        )
        if updates
        else 0
    )
    return len(updates), written, failed


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


def _type_plan(pseudo_client: ClientRecord) -> List[Tuple[str, None]]:
    """The requirement's property types, one entry each — with no size
    against any of them, because a requirement has nowhere to state one (see
    _as_pseudo_client).

    A requirement naming SEVERAL types ("Flat, Row House") means all of them
    are acceptable, which is exactly what a client's own multi-select means,
    so it is scored the same way: each type on its own, best result kept,
    tagged with the type it was for (MatchScore.matched_type) so the matches
    dialog can split the shortlist one tab per type. One type, or none, is
    read whole, exactly as a client's single type is.

    The scoring itself reads this off scoring.ClientBrief now; this is kept
    for the one question _fingerprint below still asks of it — does this
    requirement name more than one type?"""
    return [(group, None) for group in normalization.split_type_groups(pseudo_client.property_type)]


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
    brief = scoring.build_brief(pseudo_client, vector)
    best: Dict[str, MatchScore] = {}
    for prop in candidates:
        score = scoring.score_client_property(prop, brief)
        if score is None:
            continue
        previous = best.get(score.record_id)
        # Ranked the same way a client's shortlist is (match score first, then
        # confidence — scoring.ranking_key), so the two match surfaces can
        # never order the same two properties differently.
        if previous is None or scoring.ranking_key(score) > scoring.ranking_key(previous):
            best[score.record_id] = score
    return best_matches(list(best.values()))


def best_matches(scores: List[MatchScore]) -> List[MatchScore]:
    """The highest-RANKED MAX_MATCHES_PER_REQUIREMENT of `scores` — the demand
    side's half of the same ceiling a client's shortlist has always had, and
    applied the same way (scoring.ranking_key, never an arbitrary hundred).

    It was missing here, and that was a live problem waiting for the first
    busy week. A requirement naming only a budget matches most of the property
    list, so with 1,600 listings and the 1,000-requirement window the nightly
    catch-up covers, the demand side alone could have grown past a million
    rows in Neon — every one of them read back on a dialog open, and none of
    them ever looked at past the first screen. Public so the store's
    in-memory fallback applies the identical rule."""
    if len(scores) <= match_config.MAX_MATCHES_PER_REQUIREMENT:
        return scores
    return heapq.nlargest(match_config.MAX_MATCHES_PER_REQUIREMENT, scores, key=scoring.ranking_key)


# Appended to a MULTI-TYPE requirement's fingerprint, and to nothing else.
#
# The fingerprint answers "is what is stored still the result of scoring this
# requirement?", and for a requirement naming several types the answer changed
# when _type_plan above started scoring each type on its own. Marking that
# makes every such requirement re-score itself once, on its next read or on
# the next nightly catch-up, which is also what fills in the matched_type its
# type tabs split on. A requirement with one type or none is scored by exactly
# the same code as before, so its fingerprint is left untouched and it is
# never re-scored for nothing.
_PER_TYPE_SCORING_MARKER = "|| per-type scoring v1"

# Appended to EVERY requirement's fingerprint, and bumped whenever the
# scoring engine itself changes what a stored match means.
#
# The fingerprint's question is "is what is stored still the result of
# scoring this requirement?" — and the answer stops being yes when the
# engine changes, not only when the requirement's own words do. Naming the
# engine here is what makes every stored requirement re-score itself exactly
# once, on its next read or on the next nightly catch-up, with no migration,
# no extra pass and no flag to remember to unset. The client side has no
# fingerprint to lean on, so it gets the one-time pass in
# ClientPropertyMatchingService/scheduled_recompute_service.py instead.
#
# v2: eligibility gates (buy/rent, kind of property, grossly over budget,
# far-off BHK), a stated requirement the listing cannot answer priced as an
# unknown rather than dropped, a bucket ceiling set by how complete the
# requirement is, and a target band under a stated maximum.
#
# v3: the bucket ceiling is GONE. How well a property matches and how much we
# know are two separate numbers now — the match score says only how well the
# property fits what was asked for, and a thin brief lowers CONFIDENCE
# instead of being made to lower the score. Also: BHK eligibility in bedrooms
# rather than in score, tiered location relevance instead of a single
# same/different test, property type as a small ranking weight on top of its
# hard compatibility check, a stated size the listing cannot answer priced as
# an unknown, and new bucket cutoffs. See
# ClientPropertyMatchingService/scoring.py and match_config.py.
#
# v4: STRICTER, on the three things that were letting almost every
# requirement fill its hundred-row shortlist. Budget is now a hard filter on
# both sides of the client's target band by the same ratio each way (a
# ceiling-only brief is ₹65L–₹1cr eligible from ₹58.5L to ₹1.10cr; a
# floor-only brief finally has a top at all), the semantic field carries
# more weight and budget slightly less, and an area name is matched as whole
# words so "Pal" stops matching "Palanpur" while "Sarthana" still matches
# "Sarthana Jakatnaka". See ClientPropertyMatchingService/match_config.py.
_ENGINE_MARKER = "|| matching engine v4"


def _fingerprint(pseudo_client: ClientRecord) -> str:
    """sha256 of the exact text the requirement is scored and embedded from,
    plus the engine that scored it. Every field scoring reads — buy/rent,
    type, BHK, budget, areas, society, furnishing, description — is part of
    that text, so any change to them changes this."""
    text = client_requirement_text_builder.build_requirement_text(pseudo_client)
    if len(_type_plan(pseudo_client)) > 1:
        text = f"{text} {_PER_TYPE_SCORING_MARKER}"
    return hashlib.sha256(f"{text} {_ENGINE_MARKER}".encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _has_criteria(pseudo_client: ClientRecord) -> bool:
    """Does this requirement actually ASK for anything?

    The requirement-side replacement for matching_service.has_requirements,
    and it exists for one reason: `purpose` is the one ClientRecord field a
    pseudo-client ALWAYS has. _as_pseudo_client derives it from
    listing_type, which is a Literal defaulting to "Sale" — so a requirement
    can never not have one, so has_requirements could never answer False
    here, so a completely empty requirement was scored against every stored
    property and came back with a hundred "Low" matches ranked on semantic
    noise alone.

    Every OTHER field has_requirements looks at is checked here, unchanged
    and in the same order, so a requirement stating anything at all — a
    type, a BHK, a budget, an area, a furnishing level, or nothing but a
    free-text description (which reaches additional_requirements and is
    exactly what the semantic half of the score is for) — still matches
    precisely as it did before. The client side's own function is not
    touched: a client really can state only a purpose, and that is a real
    requirement there.

    False makes _build_result report has_requirements=false, which the
    matches dialog already renders as "Nothing to match on" (see
    RequirementMatchesDialog.tsx) — the empty state was written for this
    case and simply never fired."""
    return any(
        [
            pseudo_client.property_type,
            pseudo_client.bhk,
            pseudo_client.budget_min_inr is not None,
            pseudo_client.budget_max_inr is not None,
            pseudo_client.preferred_areas,
            pseudo_client.additional_requirements,
            pseudo_client.property_sizes,
            pseudo_client.furnishing,
        ]
    )


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
        arrive comma-separated ("Flat, Row House") and are scored one at a
        time, each on its own gate — see _type_plan.
      - furnishing: copied straight across. Both sides are written with the
        same three words (normalization.FURNISHING_OPTIONS), so the scoring
        engine's furnishing field compares them without knowing which side
        is a requirement and which is a client.
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
      - property_sizes: the size wanted against each requested type, passed
        straight through. It is the same shape, keyed the same way and read
        by the same parser as a client's own (see
        StructuredRequirement.property_sizes), so a size a broker stated
        scores exactly as a size a client stated does — no second code path,
        no second set of rules. Only the Add/Edit dialog ever fills it; a
        size a broker wrote in WhatsApp still lives in the description and
        still reaches the score through the semantic half, exactly as
        before.

    Deliberately absent: `notes`. It has no counterpart on ClientRecord and
    is never folded into `extra` below — a staff-only catch-all must never
    reach the embedding text or the score, exactly like ClientRecord.notes
    on the client side (see StructuredRequirement.notes).

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
        property_sizes=requirement.property_sizes,
        bhk=requirement.bhk,
        furnishing=requirement.furnishing,
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
    does not. A cache miss also persists the fresh vector to
    BrokerRequirementRow.embedding (requirement_store.save_requirement_embedding)
    — a no-op if no database is configured, and otherwise one small write per
    new-or-changed requirement, never per read."""
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
    requirement_store.save_requirement_embedding(requirement.record_id, vector)
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
        group.sort(key=scoring.ranking_key, reverse=True)

    return RequirementMatchResult(
        record_id=requirement.record_id,
        requirement_summary=_summarize(requirement),
        has_requirements=_has_criteria(pseudo_client),
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
        requirement.furnishing,
        "to rent" if requirement.listing_type == "Rent" else "to buy",
        ", ".join(areas) if areas else None,
    ]
    return " · ".join(part for part in parts if part)
