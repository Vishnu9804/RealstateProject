"""Resolves a property's instagram_reel_url to Instagram's own numeric
media id, and matches an incoming comment/DM-share back to the property it
belongs to.

Everything the comment/DM poller needs to match and reply is read from the
shared property snapshot (Service/WhatsAppDataFetchingService/property_snapshot.py),
so an idle poll cycle performs no database query at all. That is the whole
point of this module's design, so it is worth stating why:
instagram_polling_service runs a cycle every few seconds for the life of the
process, and every read this module performs is therefore performed ~10,000
times a day whether or not a single person has commented. The original
version re-read the full property list (every column of up to 1000 rows —
including `image_urls`, which holds base64 photo data measured in megabytes
per row) once per cycle, and then issued one more query per tracked property
per cycle just to look up a media id it had already looked up eight seconds
earlier. Against a serverless Postgres that bills for compute time, that
alone was enough to keep the database awake around the clock, forever,
without a single Instagram event to show for it.

So:

  - The tracked set (the _TRACKED_REEL_LIMIT most recently linked reels,
    with their media ids and the fields a reply needs) is read straight from
    the snapshot, which the property store updates as part of each write.
    This module keeps no cache of its own — a second copy of rows the
    snapshot already holds would only be one more thing able to go stale.
  - A reel that isn't in that set is not simply unanswerable: every
    reel-linked property in memory is checked, and anything still unmatched
    falls through to a narrow, targeted lookup against the database (see
    find_property_by_reel_code / find_property_by_media_pk). Those run only
    on a genuine miss, so an older reel still gets its reply — waking the
    database for a real person is exactly what it should be woken for.
  - Media-id resolution (a real Instagram API call) still happens at most
    once per property ever: the result is written back to
    PropertyRow.instagram_media_pk AND into the snapshot, so neither the API
    nor the database is asked for it again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.InstagramInquiryHandlingService import instagram_connection_service
from Service.WhatsAppDataFetchingService import property_vector_store

# How many reels the poller actively watches for new comments.
#
# This is a real bound, not an arbitrary one, and it is bounded on the
# INSTAGRAM side rather than the database side: phase 1 of every poll cycle
# makes one comments call per tracked reel, so this number multiplied by the
# poll rate IS the request rate against Instagram's private API from a single
# account. instagrapi is unofficial, and a sustained rate that climbed with
# every reel ever linked is precisely what earns an account a checkpoint (the
# same risk instagram_connection_service.py documents). Ten most-recent reels
# keeps that rate flat no matter how many properties accumulate, and recent
# reels are where comment activity actually is.
#
# Older reels are NOT abandoned: a DM share of one is still matched and
# answered through the lookup path below. Raising this number is a one-line
# change, at a proportional cost in Instagram API calls per cycle.
_TRACKED_REEL_LIMIT = 10

# Matches the short code out of any instagram.com/{reel,reels,p}/{code}/...
# URL — used to identify a shared reel WITHOUT an extra API call. Confirmed
# against a real share: Instagram delivers a reel shared into DM as an
# "xma_clip" message whose xma_share.video_url is the reel's own permalink
# (e.g. "https://www.instagram.com/reel/DC4P0w1ilgK/?id=..."), not a numeric
# media id — so matching on the code is both simpler and faster than
# resolving media pks for this path (media pk resolution is still needed
# separately for reading comments, which the API only accepts a media id
# for).
_REEL_CODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p)/([A-Za-z0-9_-]+)")


def extract_reel_code(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _REEL_CODE_RE.search(url)
    return match.group(1) if match else None


@dataclass
class TrackedReel:
    """One watched reel: the property itself, plus its Instagram media id
    alongside rather than inside it (instagram_media_pk is deliberately not
    a field on EmbeddedProperty — see Database/models.py).

    `media_pk` is mutable on purpose: resolve_media_pk fills it in the first
    time it succeeds, and because callers share these objects rather than
    copies, every later cycle sees the resolved id without going back to
    either Instagram or the database.
    """

    prop: EmbeddedProperty
    media_pk: Optional[str] = None


# Every reel-linked property the process holds in memory. Used only on the
# miss path below, where "is this one of the OLDER reels?" is asked about a
# reel the poller isn't actively watching.
_ALL_REELS_LIMIT = 5000


def _reels_from_memory(limit: int) -> List[TrackedReel]:
    """Reel-linked properties, newest link first, read from the shared
    property snapshot (Service/WhatsAppDataFetchingService/property_snapshot.py).

    This module deliberately keeps no cache of its own. It used to, and
    before that it re-queried the property table on every poll cycle — but
    a second in-memory copy of rows the snapshot already holds is just one
    more thing that can be stale in a way the first one isn't. The snapshot
    is updated by the same call that writes a property, so reading straight
    through to it is both simpler and fresher than any refresh rule this
    module could implement for itself.
    """
    return [
        TrackedReel(prop=prop, media_pk=media_pk)
        for prop, media_pk in property_vector_store.get_recent_instagram_reel_properties(limit)
    ]


def get_tracked_reels() -> List[TrackedReel]:
    """The reels the poller watches — the most recently linked ones, from
    memory. No database query, on any cycle."""
    return _reels_from_memory(_TRACKED_REEL_LIMIT)


def resolve_media_pk(tracked: TrackedReel) -> Optional[str]:
    """The reel's Instagram media id: from memory if it's already known,
    otherwise resolved once via the API and written back to both the
    database and this entry. None if not connected, the property has no reel
    link, or the URL can't be resolved (e.g. it was mistyped)."""
    if tracked.media_pk:
        return tracked.media_pk
    prop = tracked.prop
    if not prop.instagram_reel_url:
        return None

    client = instagram_connection_service.get_client()
    if client is None:
        return None
    try:
        media_pk = client.media_pk_from_url(prop.instagram_reel_url)
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(
            f"Could not resolve Instagram reel link for property {prop.record_id!r} "
            f"({prop.instagram_reel_url!r}): {exc!r}"
        )
        return None

    property_vector_store.set_instagram_media_pk(prop.record_id, str(media_pk))
    tracked.media_pk = str(media_pk)
    return tracked.media_pk


def find_property_by_media_pk(media_pk: str) -> Optional[EmbeddedProperty]:
    """A reel shared in the legacy "clip" shape, identified by numeric media
    id. Every reel-linked property held in memory is checked first — not
    just the watched ones — so the database is reached only for a property
    old enough to have fallen outside the snapshot entirely."""
    for tracked in _reels_from_memory(_ALL_REELS_LIMIT):
        if tracked.media_pk == media_pk:
            return tracked.prop
    return _find_older_property_by_media_pk(media_pk)


def find_property_by_reel_code(code: str) -> Optional[EmbeddedProperty]:
    """A reel shared in the current "xma_clip" shape, identified by the short
    code in its permalink. Memory first, database only on a true miss."""
    for tracked in _reels_from_memory(_ALL_REELS_LIMIT):
        if extract_reel_code(tracked.prop.instagram_reel_url) == code:
            return tracked.prop
    for record_id, reel_url, _ in property_vector_store.get_reel_link_index():
        if extract_reel_code(reel_url) == code:
            return _load_property(record_id)
    return None


def _find_older_property_by_media_pk(media_pk: str) -> Optional[EmbeddedProperty]:
    """The miss path for a legacy "clip" share.

    Two stages, mirroring what the old per-cycle scan did — except that this
    runs only when a real share matched nothing in memory, instead of on
    every cycle:

      1. Compare against the media ids already resolved and stored. One
         narrow query, no property content.
      2. Only if that finds nothing, resolve the reels that have never had
         their media id resolved (an older property that has never been in
         the watched set has no stored id to compare against). Each
         resolution is persisted, so this shrinks toward doing nothing and
         can never re-resolve the same property twice.
    """
    index = property_vector_store.get_reel_link_index()
    for record_id, _, stored_media_pk in index:
        if stored_media_pk == media_pk:
            return _load_property(record_id)

    client = instagram_connection_service.get_client()
    if client is None:
        return None
    for record_id, reel_url, stored_media_pk in index:
        if stored_media_pk or not reel_url:
            continue
        try:
            resolved = str(client.media_pk_from_url(reel_url))
        except Exception as exc:  # noqa: BLE001
            step_logger.warn(
                f"Could not resolve Instagram reel link for property {record_id!r} ({reel_url!r}): {exc!r}"
            )
            continue
        property_vector_store.set_instagram_media_pk(record_id, resolved)
        if resolved == media_pk:
            return _load_property(record_id)
    return None


def _load_property(record_id: str) -> Optional[EmbeddedProperty]:
    """Loads the one property a lookup matched, without its photos or
    embedding — the reply only ever quotes area/price/carpet area/BHK/type
    (see instagram_message_templates.build_property_info_message)."""
    found: Optional[Tuple[EmbeddedProperty, Optional[str]]] = property_vector_store.get_instagram_reel_property(
        record_id
    )
    return found[0] if found is not None else None
