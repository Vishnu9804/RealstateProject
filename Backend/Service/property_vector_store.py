"""Placeholder for the future Postgres+pgvector properties table (the
database step). In-memory for now, but its functions — add_property,
find_top_candidates, get_all_properties, get_property_count — are exactly
what the database step will implement against pgvector instead of a plain
Python list. Nothing that calls this module (duplicate_detection_service.py,
property_pipeline_service.py) needs to change when that swap happens.

This is also, deliberately, the ONLY place accepted (or under-review)
properties are held — the same list this module searches for similarity is
the same list the API reads for display. There is no second, separate
"already-stored" copy to keep in sync.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np

from Model.embedded_property import EmbeddedProperty

_MAX_STORED_PROPERTIES = 1000

_properties: List[EmbeddedProperty] = []


def add_property(prop: EmbeddedProperty) -> None:
    _properties.append(prop)
    if len(_properties) > _MAX_STORED_PROPERTIES:
        del _properties[: len(_properties) - _MAX_STORED_PROPERTIES]


def find_top_candidates(vector: List[float], k: int) -> List[Tuple[EmbeddedProperty, float]]:
    """Returns up to `k` existing properties ranked by whole-property
    embedding similarity, highest first — RETRIEVAL only. Brute-force at
    this in-memory scale; the database step replaces this with an indexed
    pgvector `ORDER BY embedding <=> query LIMIT k` query, which is the
    whole reason storing normalized vectors matters (see
    embedding_service.py: normalized vectors turn cosine similarity into a
    plain dot product, identical math on both sides of that swap).

    The final duplicate/new decision is NOT made here — see
    Service/duplicate_detection_service.py, which re-ranks these candidates
    by a field-level score instead of trusting this ordering directly."""
    if not _properties:
        return []
    query = np.array(vector)
    scored = [(candidate, float(np.dot(query, np.array(candidate.embedding)))) for candidate in _properties]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


def get_all_properties(limit: int = 100) -> List[EmbeddedProperty]:
    return list(_properties[-limit:])


def get_property_count() -> int:
    return len(_properties)
