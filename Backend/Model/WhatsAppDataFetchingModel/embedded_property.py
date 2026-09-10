from typing import List

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty


class EmbeddedProperty(StructuredProperty):
    """A StructuredProperty plus the vector embedding computed from it.

    Read by exactly one thing: the client-property matching feature's
    semantic score (Service/ClientPropertyMatchingService/scoring.py), which
    compares this against a client's own requirement vector. It is computed
    once, right after structuring (Service/WhatsAppDataFetchingService/
    property_pipeline_service.py), recomputed only when a human edits the
    property's content, and is the exact vector stored in the database's
    `vector` column — never re-derived at read time.

    Not exposed through the public API (see PropertyRecord) — an internal
    detail with no use in a UI.
    """

    embedding: List[float]
    embedding_model: str
