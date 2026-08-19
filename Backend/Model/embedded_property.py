from typing import List

from Model.structured_property import StructuredProperty


class EmbeddedProperty(StructuredProperty):
    """A StructuredProperty plus the vector embedding computed from it.

    This is the exact vector used for duplicate-detection similarity search
    (next step) and the exact vector later written to the database's
    `vector` column (database step) — never recomputed at either point, and
    never exposed through the public API (see PropertyRecord), since it's
    an internal detail with no use in a UI.
    """

    embedding: List[float]
    embedding_model: str
