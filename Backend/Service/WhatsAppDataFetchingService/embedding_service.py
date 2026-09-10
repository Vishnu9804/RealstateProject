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

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

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
    the raw text are noise; embedding only the identifying fields, in a
    fixed order, keeps the vector focused on what the property actually is.

    Its one consumer is the client-property matching feature's semantic
    score (Service/ClientPropertyMatchingService/scoring.py), where it acts
    as a low-weight sanity signal on top of the explicit budget/location/
    BHK scoring."""
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


def embed_text(text: str) -> List[float]:
    """Normalized to unit length, so cosine similarity reduces to a plain
    dot product — cheaper to compute (which is what scoring.py's semantic
    score relies on) and exactly what pgvector's cosine-distance index is
    built for."""
    model = _get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_property(prop: StructuredProperty) -> List[float]:
    return embed_text(build_embedding_text(prop))
