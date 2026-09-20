"""Matches an Instagram event back to the property it is about.

Two questions, and each arrives with a different kind of identifier:

  - A COMMENT webhook names the media it was left on by Instagram media id
    (`value.media.id`).
  - A REEL SHARED INTO DM names it by `attachments[].payload.reel_video_id`,
    or — depending on the sending client — only by a permalink in
    `payload.url`.

A property, meanwhile, is linked to a reel by its PERMALINK
(PropertyRow.instagram_reel_url), which contains a short code. So the whole
job of this module is turning an id into that short code, and the short code
into a property.

How the id becomes a short code is the part worth explaining. The obvious
approach — list the account's media and search it — is wrong here: it costs
a paginated crawl of up to hundreds of posts, has to be repeated as the
account posts more, and still misses anything older than the crawl. Instead
this asks Instagram about the one media id it actually has
(`GET /{media-id}?fields=permalink`), which is a single tiny call that works
for any media of any age, and then remembers the answer forever. So each
distinct reel costs exactly ONE extra API call, once, for the life of the
process — and nothing at all after that, however many comments it collects.

Nothing in here reads the database on the happy path. The set of
reel-linked properties comes from the shared in-memory property snapshot
(Service/WhatsAppDataFetchingService/property_snapshot.py), which the
property store keeps current as part of each write. A narrow database
lookup happens only when a real event names a reel old enough to have
fallen outside that snapshot — waking a serverless database for a real
customer is exactly what it should be woken for.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import List, Optional, Tuple

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.InstagramInquiryHandlingService import instagram_connection_service
from Service.InstagramInquiryHandlingService import instagram_graph_client as graph
from Service.InstagramInquiryHandlingService.instagram_graph_client import InstagramApiError
from Service.WhatsAppDataFetchingService import property_vector_store

# Matches the short code out of any instagram.com/{reel,reels,p,tv}/{code}/...
# URL. Both sides of the match go through this: the permalink stored on a
# property, and the permalink Instagram reports for a media id. Comparing
# codes rather than whole URLs is what makes them comparable at all — the
# same reel is written with and without "www.", with and without a trailing
# slash, and with assorted tracking query strings.
_REEL_CODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")

# How many reel-linked properties are scanned in memory before falling
# through to the database. Well above any realistic listing count, so the
# fallback is effectively reserved for a snapshot that has been trimmed.
_ALL_REELS_LIMIT = 5000

# media id -> short code, and the ids already known NOT to resolve. Both are
# bounded and both are only ever populated by a real incoming event, so on a
# quiet account they stay empty.
#
# The negative side matters as much as the positive one: without it, a
# comment on a post that is not linked to any property (an ordinary post on
# the client's feed) would re-ask Instagram for its permalink on every
# single comment it ever receives.
_CACHE_LIMIT = 4_000
_cache_lock = threading.Lock()
_media_code: "OrderedDict[str, str]" = OrderedDict()
_media_unresolvable: "OrderedDict[str, None]" = OrderedDict()


def extract_reel_code(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _REEL_CODE_RE.search(url)
    return match.group(1) if match else None


def _remember(table: "OrderedDict", key: str, value=None) -> None:
    table[key] = value
    table.move_to_end(key)
    while len(table) > _CACHE_LIMIT:
        table.popitem(last=False)


# --- media id -> short code ----------------------------------------------


def shortcode_for_media_id(media_id: str) -> Optional[str]:
    """The short code of the media with this id, asking Instagram at most
    once per id for the life of the process."""
    media_id = str(media_id)
    with _cache_lock:
        cached = _media_code.get(media_id)
        if cached is not None:
            _media_code.move_to_end(media_id)
            return cached
        if media_id in _media_unresolvable:
            return None

    token = instagram_connection_service.get_access_token()
    if token is None:
        return None
    try:
        body = graph.graph_get(media_id, token=token, params={"fields": "permalink"}) or {}
    except InstagramApiError as exc:
        instagram_connection_service.note_api_error(exc, f"resolving the permalink of media {media_id}")
        # Code 100 is Meta's "there is no such object, or it has no
        # permalink" — a real, stable answer of "no", so it is cached like
        # any other. Every other failure (a timeout, a rate limit, a 500) is
        # a failure to ASK rather than an answer, and caching one of those
        # would make a single bad minute permanent for that reel.
        if exc.code == 100:
            with _cache_lock:
                _remember(_media_unresolvable, media_id)
        return None

    code = extract_reel_code(body.get("permalink"))
    with _cache_lock:
        if code:
            _remember(_media_code, media_id, code)
        else:
            _remember(_media_unresolvable, media_id)
    return code


def note_unmatched_media(media_id: str) -> None:
    """Records that this media resolved fine but belongs to no property, so
    the next comment on it is answered from memory instead of costing
    another permalink lookup. Called by the event handler rather than
    inferred here, because "no property matches" is a question about the
    property list, not about Instagram."""
    with _cache_lock:
        _remember(_media_unresolvable, str(media_id))


# --- short code -> property ----------------------------------------------


def _reels_from_memory(limit: int) -> List[Tuple[EmbeddedProperty, Optional[str]]]:
    """Reel-linked properties, newest link first, straight from the shared
    property snapshot — no query, on any path."""
    return property_vector_store.get_recent_instagram_reel_properties(limit)


def find_property_by_reel_code(code: str) -> Optional[EmbeddedProperty]:
    """Memory first, database only on a true miss."""
    for prop, _media_pk in _reels_from_memory(_ALL_REELS_LIMIT):
        if extract_reel_code(prop.instagram_reel_url) == code:
            return prop
    for record_id, reel_url, _stored in property_vector_store.get_reel_link_index():
        if extract_reel_code(reel_url) == code:
            return _load_property(record_id)
    return None


def find_property_by_media_id(media_id: str) -> Optional[EmbeddedProperty]:
    """The comment path, and the reel-share path when Instagram gave a
    numeric id rather than a link."""
    code = shortcode_for_media_id(media_id)
    if code is None:
        return None
    prop = find_property_by_reel_code(code)
    if prop is None:
        note_unmatched_media(media_id)
    return prop


def find_property_by_url(url: Optional[str]) -> Optional[EmbeddedProperty]:
    """The reel-share path when the attachment carried a permalink. Returns
    None for a CDN/lookaside URL, which carries no short code — the caller
    falls back to the media id for those."""
    code = extract_reel_code(url)
    if code is None:
        return None
    return find_property_by_reel_code(code)


def _load_property(record_id: str) -> Optional[EmbeddedProperty]:
    """Loads the one property a database lookup matched, without its photos
    or embedding — the reply only ever quotes area/price/carpet area/BHK/
    type (see instagram_message_templates.build_property_info_message)."""
    found = property_vector_store.get_instagram_reel_property(record_id)
    return found[0] if found is not None else None


def describe_cache() -> str:
    """One line for the logs/status — how many permalink lookups this
    process has been saved from repeating."""
    with _cache_lock:
        return f"{len(_media_code)} resolved, {len(_media_unresolvable)} known-irrelevant"


# Kept so a stale import cannot silently start a full Instagram crawl: the
# old polling design asked this module for "the reels to watch" every few
# seconds. Nothing watches anything any more — events arrive on their own.
def get_tracked_reels() -> List[Tuple[EmbeddedProperty, Optional[str]]]:  # pragma: no cover - compatibility shim
    step_logger.warn(
        "instagram_reel_matcher.get_tracked_reels() was called, but Instagram no longer polls for "
        "comments — events arrive by webhook. This call did nothing."
    )
    return []
