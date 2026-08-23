from typing import Dict, List

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty


class EmbeddedProperty(StructuredProperty):
    """A StructuredProperty plus the vector embeddings computed from it.

    `embedding` is the whole-property vector, used only for candidate
    RETRIEVAL (Service/WhatsAppDataFetchingService/property_vector_store.py) and the exact vector later
    written to the database's `vector` column (database step) — never
    recomputed at either point.

    `field_embeddings` holds one additional vector per semantic field
    (society_name/address/area_name — see Service/WhatsAppDataFetchingService/embedding_service.py's
    FIELD_EMBEDDING_NAMES), computed once at the same time, so the
    field-level duplicate comparison (Service/WhatsAppDataFetchingService/duplicate_detection_service.py)
    never re-embeds an existing property's fields when comparing a new one
    against it.

    Neither is exposed through the public API (see PropertyRecord) — both
    are internal details with no use in a UI.
    """

    embedding: List[float]
    field_embeddings: Dict[str, List[float]] = {}
    embedding_model: str
