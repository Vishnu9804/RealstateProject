"""Resolves a property's instagram_reel_url to Instagram's own numeric
media id, and matches an incoming comment/DM-share back to the property it
belongs to. The resolution (a real API call) happens at most once per
property — the result is cached on PropertyRow.instagram_media_pk (see
Database/models.py) via Service/WhatsAppDataFetchingService/
property_vector_store.py's get/set_instagram_media_pk, so a poll cycle that
finds nothing new never has to re-resolve every tracked reel's URL.
"""

from __future__ import annotations

import re
import threading
from typing import List, Optional

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.InstagramInquiryHandlingService import instagram_connection_service
from Service.WhatsAppDataFetchingService import property_vector_store

_LIST_LIMIT = 1000

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


# The tracked-property list, snapshotted once per poll cycle.
#
# Without this it was re-read from the store on EVERY call, and the callers
# below are called per comment and per DM message — so a cycle with a
# handful of properties and a busy inbox quietly issued dozens of identical
# full-table reads, all of which had to finish before the first reply could
# be sent. That cost is invisible in the logs (it shows up only as the whole
# cycle taking longer) and it grows with both the property count and the
# message count, which is exactly the "it gets slower the more it has to
# do" behavior worth removing.
_snapshot_lock = threading.Lock()
_snapshot: Optional[List[EmbeddedProperty]] = None


def refresh_tracked_properties() -> List[EmbeddedProperty]:
    """Re-reads the tracked-property list and caches it for the rest of the
    cycle. Called once at the top of each poll cycle
    (instagram_polling_service._poll_once), so a property added mid-run is
    picked up on the next cycle — at most ~8s later."""
    global _snapshot
    properties = [
        prop for prop in property_vector_store.get_all_properties(limit=_LIST_LIMIT) if prop.instagram_reel_url
    ]
    with _snapshot_lock:
        _snapshot = properties
    return properties


def get_properties_with_reel_link() -> List[EmbeddedProperty]:
    with _snapshot_lock:
        if _snapshot is not None:
            return _snapshot
    # No cycle has run yet (e.g. a caller outside the poller) — read through.
    return refresh_tracked_properties()


def resolve_media_pk(prop: EmbeddedProperty) -> Optional[str]:
    """Returns the cached media pk if one's already stored; otherwise
    resolves it from prop.instagram_reel_url via the live client and caches
    it. None if not connected, the property has no reel link, or the URL
    can't be resolved (e.g. it was mistyped)."""
    cached = property_vector_store.get_instagram_media_pk(prop.record_id)
    if cached:
        return cached
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
    return str(media_pk)


def find_property_by_media_pk(media_pk: str) -> Optional[EmbeddedProperty]:
    for prop in get_properties_with_reel_link():
        if resolve_media_pk(prop) == media_pk:
            return prop
    return None


def find_property_by_reel_code(code: str) -> Optional[EmbeddedProperty]:
    for prop in get_properties_with_reel_link():
        if extract_reel_code(prop.instagram_reel_url) == code:
            return prop
    return None
