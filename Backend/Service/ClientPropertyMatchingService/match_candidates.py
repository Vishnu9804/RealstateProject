"""What gets matched: every listing the matching engine scores against a
client inquiry or a broker requirement — the stored PROPERTIES and the
BUILDER PROJECTS — handed out as one list, in the one shape scoring takes
(EmbeddedProperty). The only place the two sources are brought together, so
the client side (matching_service.py), the broker-requirement side
(Service/BrokerRequirementService/requirement_matching_service.py) and the
daily rescore (scheduled_recompute_service.py) can never disagree about
what a "match candidate" is.

Both halves come from memory, never from a query:

  - properties from the property snapshot (Service/WhatsAppDataFetchingService/
    property_snapshot.py, via property_vector_store), up to MAX_PROPERTIES;
  - builder projects from the Builder Projects cache (Service/
    BuilderProjectService/builder_project_store.py), up to
    MAX_BUILDER_PROJECTS — the same 5000 ceiling.

A builder project arrives as a BuilderProjectCandidate (a subclass of
EmbeddedProperty), which is how source_of tells the two apart without a
lookup, and how every match result says which kind of listing it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from Model.BuilderProjectModel.builder_project_candidate import (
    BUILDER_PROJECT_SOURCE,
    PROPERTY_SOURCE,
    BuilderProjectCandidate,
)
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.BuilderProjectService import builder_project_store
from Service.WhatsAppDataFetchingService import property_vector_store

# How many of each are scored per pass. The property table is WhatsApp-
# sourced and, at this project's scale, realistically in the hundreds to low
# thousands — scoring every held row directly in Python is simpler than a
# pgvector top-K pre-filter and fast enough at this size. Builder projects
# are hand-entered, so their cache (the same ceiling) is in practice the
# whole table.
MAX_PROPERTIES = 5000
MAX_BUILDER_PROJECTS = builder_project_store.CACHE_LIMIT


def get_all(ensure_embeddings: bool = True) -> List[EmbeddedProperty]:
    """Every held property, then every held builder project.

    `ensure_embeddings=False` is for callers that only read display fields
    (building a result from already-stored scores): it never runs the
    embedding model for a builder project that has no vector yet. Every
    caller that SCORES leaves it on."""
    return property_vector_store.get_all_properties(limit=MAX_PROPERTIES) + builder_project_store.get_match_candidates(
        MAX_BUILDER_PROJECTS, ensure_embeddings=ensure_embeddings
    )


def get_changed_since(since: Optional[datetime]) -> List[EmbeddedProperty]:
    """Properties and builder projects added or edited strictly after
    `since` (everything when `since` is None) — what an incremental rescore
    looks at. Both sides compare their own updated_at, so an edit counts as
    well as an addition."""
    return property_vector_store.get_properties_changed_since(
        since, limit=MAX_PROPERTIES
    ) + builder_project_store.get_match_candidates_changed_since(since, MAX_BUILDER_PROJECTS)


def get_builder_projects() -> List[BuilderProjectCandidate]:
    """Only the builder projects — for the one-time pass that scores the
    projects that existed before builder projects were matched against every
    client and requirement already scored (see scheduled_recompute_service.
    start_builder_project_introduction_in_background)."""
    return builder_project_store.get_match_candidates(MAX_BUILDER_PROJECTS)


def source_of(prop: EmbeddedProperty) -> str:
    """"builder_project" for a builder project, "property" for everything
    else — the value every MatchedProperty carries as property_source."""
    return BUILDER_PROJECT_SOURCE if isinstance(prop, BuilderProjectCandidate) else PROPERTY_SOURCE
