"""Free, locally-run embedding stage (Stage 4 — "Free Embedding Algorithm"
in the architecture diagram). Turns a structured property into a
fixed-size vector using a local sentence-transformers model — no API key,
no per-call cost, and nothing sent over the network at inference time
(only the one-off model download on first use).

Critical design constraint, driving everything in this file: this is the
ONLY place a property is ever embedded. The vector computed here is the
exact same vector later written into Postgres's `vector` column (database
step) — pgvector only stores and indexes vectors, it cannot generate one
itself, so whatever isn't computed here never gets computed at all. There is
no second embedding pass "at insert time"; the vector saved to the database
is this one, byte-for-byte. Its only reader is client-property match scoring
(Service/ClientPropertyMatchingService/scoring.py) — duplicates are no longer
detected by vector similarity at all, but by an exact-text fingerprint before
the LLM stage (see property_pipeline_service._drop_duplicate_messages).

Using the same model for both a property and a client's requirement is also
what makes that similarity score meaningful in the first place — vectors
from two different embedding models are not comparable, even if they happen
to share the same dimension count. EMBEDDING_MODEL_NAME and
EMBEDDING_DIMENSIONS below are the two facts every later stage (match
scoring, and the pgvector column definition) must stay in lock-step with —
both import them from here rather than hardcoding a model name or a
dimension count of their own.

The same is true of WHICH WORDS go in. Changing EMBEDDING_TEXT_FIELDS
changes what every new vector means while leaving every vector already in
Postgres built from the old text — two kinds of vector in one column,
scored against one client requirement, with nothing to tell them apart and
no error anywhere to notice it. So whenever that tuple changes, the stored
listings have to be re-embedded from it; tools/reembed_listings.py does
exactly that in one pass and is safe to re-run.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

from sentence_transformers import SentenceTransformer

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384

# The DESCRIPTIVE fields a listing's vector is built from, in this fixed
# order — see build_embedding_text. Shared by a property and a builder
# project (build_embedding_text_from_fields), so the two are embedded from
# exactly the same kind of text and land in the same semantic space as a
# client's requirement vector.
#
# WHAT IS DELIBERATELY NOT HERE: society_name, contact_name and the contact
# number. Those IDENTIFY a listing, they do not describe it, and no client
# requirement can express a preference about any of them — nobody asks for
# "a flat called Rudravan Apartment", and the agent's own phone number says
# nothing about whether a property fits.
#
# They were measured out, not guessed out. Against two near-identical 4 BHK
# flats offered to one buyer — same type, same configuration, both inside her
# budget, both for sale, so every structured field tied at 1.0 and the
# semantic field alone separated them — a leave-one-out ablation put
# society_name at +0.12 of the 0.10 similarity gap between them, more than
# the whole gap it was meant to explain, while contact_name and the number
# (the SAME strings on both listings, one agent) each still moved the score
# by ~0.01 purely from where they sat in the sentence. That gap decided a
# bucket: one flat read High, the other Medium. A field that can do that on
# the strength of a proper noun is noise carrying a weight, and scoring.py's
# semantic signal is only worth having while it reflects what a listing IS.
EMBEDDING_TEXT_FIELDS = (
    "property_type",
    "bhk",
    "area_name",
    "address",
    "price_text",
    "description",
)

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def build_embedding_text(prop: StructuredProperty) -> str:
    """Canonical text built field-by-field from the LLM-structured data —
    not the raw WhatsApp message. Broker chatter, greetings, and emojis in
    the raw text are noise; embedding only the descriptive fields, in a
    fixed order, keeps the vector focused on what the property actually is.

    Its one consumer is the client-property matching feature's semantic
    score (Service/ClientPropertyMatchingService/scoring.py), where it acts
    as a low-weight sanity signal on top of the explicit budget/location/
    BHK scoring."""
    return _join_parts(getattr(prop, name) for name in EMBEDDING_TEXT_FIELDS)


def build_embedding_text_from_fields(fields: Mapping[str, Any]) -> str:
    """build_embedding_text for a listing held as plain column values rather
    than a StructuredProperty — a builder project (Service/
    BuilderProjectService/builder_project_store.py). Same fields, same
    order, same separator, so a builder project and a property with the same
    details produce byte-for-byte the same text and the same vector."""
    return _join_parts(fields.get(name) for name in EMBEDDING_TEXT_FIELDS)


def _join_parts(parts: Iterable[Any]) -> str:
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
