"""One-time backfill: re-embed every stored listing with the CURRENT
embedding-text recipe.

Run this whenever Service/WhatsAppDataFetchingService/embedding_service.py's
EMBEDDING_TEXT_FIELDS changes. Until it has run, listings captured before
that change still carry vectors built from the OLD text, and the semantic
half of every match score is quietly comparing two different kinds of vector
against one client requirement — with no error anywhere to notice it.

    cd Backend
    venv/Scripts/python.exe tools/reembed_listings.py --dry-run   # show, write nothing
    venv/Scripts/python.exe tools/reembed_listings.py             # do it

Safe to re-run: each listing's text is rebuilt from its own current fields,
so a second pass simply writes the same vectors again.

STOP THE BACKEND FIRST, or restart it afterwards. A running process serves
properties and builder projects from in-memory caches that it refreshes on
its own schedule (properties) or only at startup (builder projects), so
until it is restarted it can go on scoring against the vectors it already
holds.

Each re-embedded property's updated_at is bumped on purpose — see
property_repository.set_embeddings. That is what tells the 6 AM incremental
pass its cached scores were computed from a vector that no longer exists, so
expect the next pass to be a full rescore rather than a quiet one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Database import builder_project_repository, property_repository  # noqa: E402
from Service.BuilderProjectService import builder_project_store  # noqa: E402
from Service.ClientPropertyMatchingService import match_candidates  # noqa: E402
from Service.WhatsAppDataFetchingService import embedding_service, property_vector_store  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv
# How many rebuilt texts a dry run prints. Enough to eyeball that the recipe
# produces what it should; not so many that the real answer scrolls away.
SAMPLE = 5
# Properties per transaction. One transaction for all of them would hold a
# write open across the whole model run; one per property would be ~1700
# round trips to a scale-to-zero database. This is the middle.
CHUNK = 200


def progress(done: int, total: int) -> None:
    if done == total or done % 100 == 0:
        print(f"  embedded {done}/{total}")


def reembed_properties() -> None:
    stored = property_vector_store.get_property_count()
    properties = property_vector_store.get_all_properties(limit=match_candidates.MAX_PROPERTIES)
    print(f"Properties: {len(properties)} loaded, {stored} in the table")
    if len(properties) < stored:
        # A partial migration is the one genuinely bad outcome here: the rows
        # left behind keep old-recipe vectors that nothing will ever flag.
        print(
            f"  STOP: {stored - len(properties)} properties are outside the load window "
            f"(match_candidates.MAX_PROPERTIES={match_candidates.MAX_PROPERTIES}). "
            "Raise it before running, or those rows keep their old vectors."
        )
        return
    if not properties:
        return

    if DRY_RUN:
        for prop in properties[:SAMPLE]:
            print(f"  {prop.record_id}: {embedding_service.build_embedding_text(prop)}")
        print(f"  ... and {max(0, len(properties) - SAMPLE)} more (nothing written)")
        return

    vectors: Dict[str, List[float]] = {}
    for index, prop in enumerate(properties, start=1):
        vectors[prop.record_id] = embedding_service.embed_property(prop)
        progress(index, len(properties))

    written = 0
    record_ids = list(vectors)
    for start in range(0, len(record_ids), CHUNK):
        batch = {record_id: vectors[record_id] for record_id in record_ids[start : start + CHUNK]}
        written += property_repository.set_embeddings(batch, embedding_service.EMBEDDING_MODEL_NAME)
        print(f"  wrote {written}/{len(vectors)}")
    if written != len(vectors):
        print(f"  WARNING: {len(vectors) - written} properties matched no row and kept their old vector")


def reembed_builder_projects() -> None:
    projects = builder_project_store.get_all(builder_project_store.CACHE_LIMIT)
    print(f"Builder projects: {len(projects)}")
    if not projects:
        return

    if DRY_RUN:
        for entry in projects[:SAMPLE]:
            print(f"  {entry.record_id}: {embedding_service.build_embedding_text_from_fields(entry.fields)}")
        print(f"  ... and {max(0, len(projects) - SAMPLE)} more (nothing written)")
        return

    for index, entry in enumerate(projects, start=1):
        text = embedding_service.build_embedding_text_from_fields(entry.fields)
        # update_project, not builder_project_repository.set_embeddings: that
        # one preserves updated_at because it exists to fill in a MISSING
        # vector, and a replaced vector has to look like the change it is, for
        # the same reason properties bump theirs.
        builder_project_repository.update_project(entry.record_id, {}, embedding=embedding_service.embed_text(text))
        progress(index, len(projects))


print(f"Recipe: {' | '.join(embedding_service.EMBEDDING_TEXT_FIELDS)}")
print(f"Model:  {embedding_service.EMBEDDING_MODEL_NAME}")
print("DRY RUN — nothing will be written\n" if DRY_RUN else "")
reembed_properties()
reembed_builder_projects()
print(
    "\nDone." if not DRY_RUN else "\nDry run complete.",
    "Restart the backend so it stops serving the vectors it already holds.",
)
