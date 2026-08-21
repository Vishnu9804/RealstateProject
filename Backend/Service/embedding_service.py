"""Free, locally-run embedding stage (Stage 4 — "Free Embedding Algorithm"
in the architecture diagram). Turns a structured property into a
fixed-size vector using a local sentence-transformers model — no API key,
no per-call cost, and nothing sent over the network at inference time
(only the one-off model download on first use).

Critical design constraint, driving everything in this file: this is the
ONLY place a property is ever embedded. The vector computed here is:
  1. compared against existing property vectors for duplicate detection
     (next step), and
  2. the exact same vector later written into Postgres's `vector` column
     (database step) — pgvector only stores and indexes vectors, it cannot
     generate one itself, so whatever isn't computed here never gets
     computed at all. There is no second embedding pass "at insert time";
     the vector saved to the database is this one, byte-for-byte.

Using the same model for both an incoming property and everything already
stored is also what makes the similarity search meaningful in the first
place — vectors from two different embedding models are not comparable,
even if they happen to share the same dimension count. EMBEDDING_MODEL_NAME
and EMBEDDING_DIMENSIONS below are the two facts every later stage (the
duplicate-check step, and the pgvector column definition after that) must
stay in lock-step with — both steps import them from here rather than
hardcoding a model name or a dimension count of their own.
"""

from __future__ import annotations

from typing import List, Optional

from sentence_transformers import SentenceTransformer

from Model.structured_property import StructuredProperty

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def build_embedding_text(prop: StructuredProperty) -> str:
    """Canonical text built field-by-field from the LLM-structured data —
    not the raw WhatsApp message. Broker chatter, greetings, and emojis in
    the raw text are noise for duplicate detection; embedding only the
    identifying fields, in a fixed order, keeps the comparison focused on
    what actually makes two listings the same property.

    This whole-property vector is used only for candidate RETRIEVAL (see
    Service/property_vector_store.py) — narrowing down which existing
    properties are worth a detailed look. The final duplicate/new decision
    is made field-by-field instead (Service/duplicate_detection_service.py),
    using the per-field vectors from FIELD_EMBEDDING_NAMES below, not this
    combined one."""
    parts = [
        prop.property_type,
        prop.bhk,
        prop.society_name,
        prop.area_name,
        prop.address,
        prop.price_text,
        prop.contact_name,
        prop.contact_phone,
        prop.description,
    ]
    return " | ".join(part for part in parts if part)


# The semantic (free-text) fields the duplicate-detection stage scores
# individually. Kept here, next to the model that produces their vectors,
# rather than in duplicate_detection_service.py.
FIELD_EMBEDDING_NAMES = ("society_name", "address", "area_name")


def embed_text(text: str) -> List[float]:
    """Normalized to unit length, so cosine similarity (what the duplicate
    check uses) reduces to a plain dot product — cheaper to compute and
    exactly what pgvector's cosine-distance index is built for."""
    model = _get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_property(prop: StructuredProperty) -> List[float]:
    return embed_text(build_embedding_text(prop))


def embed_property_fields(prop: StructuredProperty) -> dict:
    """One embedding per semantic field (society_name/address/area_name),
    computed once here and cached on EmbeddedProperty — so a later
    field-level duplicate comparison never re-embeds the "existing" side of
    the comparison, only ever the incoming property's fields."""
    return {name: embed_text(value) for name in FIELD_EMBEDDING_NAMES if (value := getattr(prop, name))}
