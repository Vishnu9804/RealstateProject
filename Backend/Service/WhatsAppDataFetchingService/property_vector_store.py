"""Storage abstraction for accepted/under-review properties — the one
place duplicate_detection_service.py and property_pipeline_service.py go
to store and search properties. They never know or care which backend is
actually active underneath:

  - DATABASE_URL unset (the default, until the final "connect the
    database" step): falls back to the in-memory brute-force
    implementation this module has had since Step 6/7 — the exact same
    code, unchanged, already covered by tests/test_duplicate_detection.py.
  - DATABASE_URL set: delegates to Database/property_repository.py
    (Postgres + pgvector).

This is also, deliberately, the ONLY place accepted/under-review properties
are held — the same data this module searches for similarity is the same
data the API reads for display. There is no second, separate copy to keep
in sync, in either mode.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np

from Database import property_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

_MAX_STORED_PROPERTIES = 1000

# In-memory fallback only — untouched whenever a database is configured.
_properties: List[EmbeddedProperty] = []


def add_property(prop: EmbeddedProperty) -> None:
    if is_database_configured():
        property_repository.add_property(prop)
        return
    _properties.append(prop)
    if len(_properties) > _MAX_STORED_PROPERTIES:
        del _properties[: len(_properties) - _MAX_STORED_PROPERTIES]


def find_top_candidates(vector: List[float], k: int) -> List[Tuple[EmbeddedProperty, float]]:
    """Returns up to `k` existing properties ranked by whole-property
    embedding similarity, highest first — RETRIEVAL only. The final
    duplicate/new decision is made field-by-field in
    Service/WhatsAppDataFetchingService/duplicate_detection_service.py, which re-ranks these candidates
    rather than trusting this ordering directly."""
    if is_database_configured():
        return property_repository.find_top_candidates(vector, k)

    if not _properties:
        return []
    query = np.array(vector)
    scored = [(candidate, float(np.dot(query, np.array(candidate.embedding)))) for candidate in _properties]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


def get_all_properties(limit: int = 100) -> List[EmbeddedProperty]:
    if is_database_configured():
        return property_repository.get_all_properties(limit)
    return list(_properties[-limit:])


def get_property_count() -> int:
    if is_database_configured():
        return property_repository.get_property_count()
    return len(_properties)
