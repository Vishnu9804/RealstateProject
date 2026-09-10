"""API shapes for the internal area knowledge base and its analysis — what
the Temporary page reads.

Mirrors the snapshot built by Service/WhatsAppDataFetchingService/
area_knowledge_service.py's get_overview(). Read-only by nature: the
knowledge base is written by the property pipeline as a side effect of
structuring, never through this API.
"""

from typing import List, Optional

from pydantic import BaseModel


class AreaKnowledgeTotals(BaseModel):
    """The headline analysis. "lookups" counts PLACE STRINGS checked, while
    "properties_seen" counts VISITS to the knowledge base (one per property
    the LLM produced) — one visit usually checks several strings, so the two
    are deliberately different numbers."""

    batches_observed: int = 0
    properties_seen: int = 0
    properties_recorded: int = 0
    properties_skipped_no_area: int = 0
    properties_all_known: int = 0
    properties_with_new_places: int = 0
    place_lookups: int = 0
    place_hits: int = 0
    place_writes: int = 0
    cross_area_collisions: int = 0
    hit_rate: float = 0.0
    area_count: int = 0
    place_count: int = 0
    first_observed_at: Optional[str] = None
    last_observed_at: Optional[str] = None
    stats_since: Optional[str] = None


class AreaKnowledgeBreakdown(BaseModel):
    """One row of a hit/write breakdown — by where the place string came from
    on the property ("area"/"address"/"society"), or by the property's own
    review_status ("accepted"/"outsider")."""

    name: str
    lookups: int = 0
    hits: int = 0
    writes: int = 0
    hit_rate: float = 0.0


class AreaKnowledgeArea(BaseModel):
    """One area's entry in the knowledge base, plus how it has performed."""

    area: str
    place_count: int = 0
    places: List[str] = []
    lookups: int = 0
    hits: int = 0
    writes: int = 0
    hit_rate: float = 0.0
    properties: int = 0
    first_seen: Optional[str] = None
    last_updated: Optional[str] = None


class AreaKnowledgeEvent(BaseModel):
    """One property's visit to the knowledge base, kept so the raw behaviour
    behind the totals can be inspected rather than taken on trust."""

    at: str
    area: Optional[str] = None
    source_message_id: Optional[str] = None
    record_id: Optional[str] = None
    review_status: Optional[str] = None
    lookups: int = 0
    hits: int = 0
    writes: int = 0
    hit_places: List[str] = []
    new_places: List[str] = []
    collisions: List[str] = []
    skipped: bool = False
    skip_reason: Optional[str] = None


class AreaKnowledgeOverview(BaseModel):
    file_path: str
    totals: AreaKnowledgeTotals
    by_source: List[AreaKnowledgeBreakdown] = []
    by_status: List[AreaKnowledgeBreakdown] = []
    areas: List[AreaKnowledgeArea] = []
    events: List[AreaKnowledgeEvent] = []
