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

MEMORY

The model is loaded on first use and released again after
`embedding_model_idle_unload_minutes` with nothing to embed (see
Config/settings.py; 0 switches releasing off and restores the old
load-once-and-keep-it-forever behaviour). Two consequences, both deliberate:

  - `import sentence_transformers` happens inside _get_model(), not at the
    top of this file. Every SQLAlchemy model that declares a vector column
    imports this module at start-up just for EMBEDDING_DIMENSIONS
    (Database/models.py and its three counterparts), so a module-level
    import pulled PyTorch into memory during boot on every single start,
    whether or not anything was ever embedded afterwards.
  - What the model PRODUCES is unchanged. Same model name, same weights,
    same normalisation — a vector computed after a reload is identical to
    one computed before it, which it has to be: vectors already stored in
    Postgres are compared directly against newly computed ones, and two
    vectors that disagree by even a rounding step would quietly shift match
    scores with no error to notice.

The cost is one reload, a second or two from the on-disk model cache, on
the first embedding after a quiet stretch. Every embedding call refreshes
the idle timer, so it is paid at most once per quiet period and never
part-way through a run of work.
"""

from __future__ import annotations

import ctypes
import gc
import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Iterable, List, Mapping, Optional

from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty

if TYPE_CHECKING:
    # Type checkers and IDEs only. `from __future__ import annotations` above
    # makes every annotation in this file a string that is never evaluated at
    # runtime, so naming SentenceTransformer below costs nothing at import
    # time — which is the entire point (see this module's docstring).
    from sentence_transformers import SentenceTransformer

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

# The loaded model, or None when it has never been loaded or has since been
# released. _model_lock guards it AND serialises loading: without it two
# threads arriving together (a WhatsApp property batch and a client save,
# say) would each build their own copy and briefly hold two models' worth of
# RAM before one of them was collected. Re-entrant to match the rest of this
# codebase's locks, and because the readers below are called from FastAPI
# request threads, the WhatsApp/Instagram background threads and the daily
# recompute thread at the same time.
_model: Optional[SentenceTransformer] = None
_model_lock = threading.RLock()
# time.monotonic(), never wall-clock time: a system clock adjustment must
# never be able to make the model look idle for hours and have it released
# out from under a working day. Same reasoning as
# property_vector_store._last_external_drift_check.
_last_used_at = 0.0
# Whether the sweeper below is alive. Read and written only under
# _model_lock, which is what makes "start one only if there isn't one"
# reliable without a second flag to get wrong.
_sweeper_running = False
# Which sweeper is the current one. A sweeper spends almost all of its life
# asleep, so if the model it was started for is released by anything other
# than that sweeper, it can wake up long after a replacement has been
# started — and clear the flag the replacement owns, which would eventually
# leave two sweepers running. Each one carries the generation it was born
# with and retires quietly if it is no longer the current one, so that
# cannot happen however the model came to be released.
_sweeper_generation = 0
# How often the sweeper wakes to look. Small next to the idle threshold it
# checks, so the model is released within about a minute of falling idle,
# and cheap enough to be irrelevant: a sleep and one subtraction.
_IDLE_CHECK_INTERVAL_SECONDS = 60.0
# Set once the model has been built successfully, which proves every file it
# needs is in the on-disk cache — see _build_model for what that buys.
_model_files_cached = False


def _idle_unload_seconds() -> float:
    """0 (or less) means never release — see Config/settings.py."""
    return get_settings().embedding_model_idle_unload_minutes * 60.0


def _return_free_memory_to_the_os() -> None:
    """Asks glibc to hand back the heap pages that releasing the model just
    freed. Best effort, and a no-op wherever it does not apply.

    Freeing a large object in Python does not necessarily shrink the
    process. glibc keeps freed blocks in its own arenas to reuse, so the
    memory stops being used without stopping being HELD — and a host that
    bills by memory held over time charges for it either way. Measured here,
    releasing the model returned only about a tenth of what loading it
    added until this ran.

    malloc_trim(0) is the supported way to ask for the rest back. It only
    ever releases pages the allocator already considers free, so it cannot
    disturb live objects; the cost is that the next allocation of that size
    faults its pages back in, which is why this is called once per release
    rather than on any hot path.

    Wrapped because it is a measurement nicety, never a requirement: a
    platform without glibc (Windows locally, musl in a slim container)
    simply does not have this symbol, and must carry on unaffected.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - reclaiming must never break a release
        pass


def _apply_hf_token() -> None:
    """Makes HF_TOKEN from `.env` visible to huggingface_hub.

    huggingface_hub reads the token from the process environment, but
    Config/settings.py loads `.env` without exporting it, so a token that
    lives only in `.env` would never be seen locally. An HF_TOKEN already set
    in the real environment (as on Railway) is left untouched, and a blank
    token changes nothing — the model is public and downloads anonymously.
    """
    token = get_settings().hf_token.strip()
    if token and not os.environ.get("HF_TOKEN"):
        os.environ["HF_TOKEN"] = token


def _build_model() -> SentenceTransformer:
    """Constructs the model, reading from the on-disk cache alone once we
    know the files are there.

    This is not a micro-optimisation; it is what makes releasing the model
    affordable at all. Left to itself, sentence-transformers asks the
    Hugging Face Hub whether the model has changed on every construction,
    and measured on this project that round trip IS the cost of a load:
    6-8 seconds with it, 0.1 seconds without. It would also put a Hub
    outage between a broker pressing Save and their property being embedded,
    for a model that cannot have changed — EMBEDDING_MODEL_NAME is pinned,
    and a model that silently changed underneath stored vectors would be a
    matching-quality disaster rather than a welcome update.

    The FIRST construction in a process is left alone, because that is the
    one that legitimately may have to download: a fresh container's
    filesystem starts empty.

    The fallback is deliberate rather than defensive habit. A cache
    populated earlier can genuinely be cleaned up underneath a long-running
    container, and the cost of being wrong about that would be an embedding
    that never succeeds again until someone restarted the process; retrying
    the way that always worked turns that into one slow load and a log line.
    """
    global _model_files_cached
    _apply_hf_token()
    from sentence_transformers import SentenceTransformer

    if _model_files_cached:
        try:
            return SentenceTransformer(EMBEDDING_MODEL_NAME, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            step_logger.warn(
                f"[Embedding] The cached copy of {EMBEDDING_MODEL_NAME} could not be read "
                f"({type(exc).__name__}) — fetching it again."
            )
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    _model_files_cached = True
    return model


def _get_model() -> SentenceTransformer:
    """The model, loading it if this is the first use since start-up or
    since it was last released.

    Holding the lock across the load is what stops two threads arriving
    together from building a model each; the second waits and then gets the
    first one's. encode() itself runs outside this function and outside the
    lock, so embeddings still proceed in parallel exactly as before.
    """
    global _model, _last_used_at
    with _model_lock:
        _last_used_at = time.monotonic()
        if _model is None:
            _model = _build_model()
            _start_idle_sweeper()
        return _model


def _start_idle_sweeper() -> None:
    """Starts the one thread that releases the model once it goes unused.
    CALLER HOLDS _model_lock.

    Started from the load, and the thread returns the moment it has released
    the model, so there is no thread at all while no model is loaded: a
    process that never embeds anything behaves exactly as it did before any
    of this existed.
    """
    global _sweeper_running, _sweeper_generation
    if _sweeper_running or _idle_unload_seconds() <= 0:
        return
    _sweeper_generation += 1
    _sweeper_running = True
    threading.Thread(
        target=_idle_sweeper_loop,
        args=(_sweeper_generation,),
        name="embedding-model-idle-unload",
        daemon=True,
    ).start()


def _idle_sweeper_loop(generation: int) -> None:
    """Releases the model once _idle_unload_seconds() has gone by with no
    call to _get_model(), then exits.

    Releasing can never interrupt work in progress. embed_text below binds
    the model to a local name before calling encode(), and that reference
    alone keeps the object alive for as long as the call runs — dropping the
    module-level reference here only means the NEXT caller loads a fresh
    one. Nothing observes a half-released model.
    """
    global _model, _sweeper_running
    while True:
        time.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        with _model_lock:
            if generation != _sweeper_generation:
                # Superseded while asleep. The current sweeper owns
                # _sweeper_running now, so this one leaves it alone.
                return
            idle_after = _idle_unload_seconds()
            if _model is None or idle_after <= 0:
                # Already gone, or releasing was switched off underneath us.
                # Either way this thread has nothing left to do, and the
                # next load starts a fresh one.
                _sweeper_running = False
                return
            if time.monotonic() - _last_used_at < idle_after:
                continue
            _model = None
            _sweeper_running = False
        # Deliberately outside the lock. A full collection is not instant,
        # and an embedding arriving at this exact moment should be waiting
        # for its own reload, not for this. The model is already unreachable
        # by the time this runs, and collecting is what actually frees it —
        # a transformer model holds reference cycles, so dropping the last
        # name above is not on its own enough to return the memory.
        gc.collect()
        _return_free_memory_to_the_os()
        step_logger.info(
            f"[Embedding] Model released after {idle_after / 60:.0f} min with nothing to embed — it reloads "
            "by itself on the next one (EMBEDDING_MODEL_IDLE_UNLOAD_MINUTES=0 turns this off)."
        )
        return


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
