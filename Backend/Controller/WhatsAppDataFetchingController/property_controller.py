"""HTTP routes for structured property data — the output of the LLM
structuring stage, and eventually the "Excel-like" dashboard data. Thin by
design; state lives in Service/WhatsAppDataFetchingService/property_pipeline_service.py.
"""

import threading
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, TypeAdapter, field_validator

from Middleware import http_cache
from Model import field_validation
from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Service.AuthManagementService.auth_dependencies import require_admin
from Service.WhatsAppDataFetchingService import display_settings_service, property_pipeline_service

router = APIRouter(prefix="/properties", tags=["properties"])

# The ceiling GET /properties will serve in one response. Deliberately the
# same number as the in-memory snapshot's own capacity (see
# Service/WhatsAppDataFetchingService/property_snapshot.py's _SNAPSHOT_LIMIT
# and match_candidates.MAX_PROPERTIES) — asking for more than the snapshot
# holds cannot return more, so clamping here makes the two agree instead of
# letting a caller believe it asked for something it cannot get.
LIST_MAX_LIMIT = 5000

# Serialises a whole page of properties in one go, producing byte-for-byte
# what FastAPI's own response_model path produces for this route.
_LIST_ADAPTER = TypeAdapter(List[PropertyRecord])

# The rendered JSON of the property list, keyed by the ETag that identifies
# it — i.e. by (limit, property version, time-format setting). The list is
# identical for every open tab and every logged-in user, it changes only
# when a property changes, and building it means serialising every held
# property. Holding the finished bytes means the second tab, the page
# revisit and the browser that dropped its cache all cost a memcpy instead
# of a full re-serialisation.
#
# Bounded to a couple of entries because a new ETag makes every older entry
# dead on arrival — this is a "the answer I just built" cache, not a
# history. Keyed by ETag rather than cleared on write so there is no
# invalidation to get wrong: an entry for a superseded version can never be
# handed out, because nothing will ever ask for that key again.
_LIST_BODY_MAX_ENTRIES = 3
_list_bodies: Dict[str, bytes] = {}
_list_bodies_lock = threading.Lock()


class PropertyContentFields(field_validation.ListingContentValidators):
    """The fields the Properties page's Add/Edit dialog exposes — the same
    set the LLM structuring stage would otherwise fill in, plus
    instagram_reel_url (set only by a human, never by the LLM). Every field
    is optional: the dialog itself has no required inputs, so a property can
    be saved with as little or as much detail as is known right now.

    Optional, but no longer unchecked. What a person types here used to be
    stored exactly as typed — an area of -100 sqft, a contact number of
    "xyz", a map pin of "not a url" and, worst of the four, an Instagram
    reel of "hello", which counted as "this property has a reel" and put it
    straight into the public landing page's Ready to Add list. The rules
    come from Model/field_validation.py and are shared with the Builder
    Projects page, which is the same dialog. They apply ONLY here, on the
    request body: a listing captured from WhatsApp is built by the LLM stage
    from whatever a broker wrote, and refusing one of those would lose a
    real message rather than correct anyone."""

    property_type: Optional[str] = None
    bhk: Optional[str] = None
    # Shown as "Unit / Flat number" — the client's two spreadsheets name this
    # column differently but mean the same thing (StructuredProperty.unit_no).
    unit_no: Optional[str] = None
    # Shown as "Society / Building name", for the same reason.
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    # An area and a price are measurements: never negative, and never a
    # number no property could have (see field_validation's own constants).
    area_sqft: Optional[float] = Field(default=None, ge=0, le=field_validation.MAX_AREA)
    area_vaar: Optional[float] = Field(default=None, ge=0, le=field_validation.MAX_AREA)
    # Human-only, like instagram_reel_url — never asked of the LLM (see
    # StructuredProperty.super_built).
    super_built: Optional[str] = None
    furnishing: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = Field(default=None, ge=0, le=field_validation.MAX_INR)
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    # The dialog's list of contact numbers, each stored as "+91" + 10
    # digits — see Model/phone_numbers.py. Optional[List] rather than a
    # plain list so a PATCH that leaves it out stays silent about it
    # (exclude_unset), which is what keeps Accept/Move from blanking a
    # listing's numbers.
    contact_phones: Optional[List[str]] = None
    # Retired, still accepted: what a browser running the previous bundle
    # sends. field_validation.bridge_contact_phones is what folds it into
    # contact_phones above, and nothing downstream ever stores it.
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    instagram_reel_url: Optional[str] = None
    # Photos of the property — never required, never touched by the LLM
    # stage. Same "optional, human-only" story as instagram_reel_url.
    image_urls: List[str] = Field(default_factory=list)
    # The rest are human-only too. location_url is the internal map pin and
    # is accepted here but never returned by any public endpoint — see
    # StructuredProperty.location_url.
    location_url: Optional[str] = None
    video_available: bool = False
    extra_notes: Optional[str] = None
    is_available: bool = True


class PropertyUpdateRequest(PropertyContentFields):
    """Every content field is inherited from PropertyContentFields (all
    optional, used by the Edit dialog); review_status and needs_review are
    the two other things the UI can change on a property (move to
    Main/Outsider, and the Needs review queue's Accept action). Any subset
    of all of these may be sent in one request — only the fields actually
    present in the JSON body are applied (see the controller's
    exclude_unset), so the Accept/Move actions (which send only their own
    field) never accidentally blank out a property's content."""

    review_status: Optional[Literal["accepted", "outsider"]] = None
    needs_review: Optional[bool] = None
    # The Landing Page page's Send/Remove actions: True publishes a property
    # (moves it into Live), False un-publishes it (moves it back to Ready to
    # Add). Sent alone, on its own PATCH, same as review_status/needs_review
    # — never bundled with a content edit from the Add/Edit dialog.
    landing_page: Optional[bool] = None
    # Overridden from the parent's plain "Sale" default to None so
    # exclude_unset can tell "left out of this PATCH" apart from "explicitly
    # set to Sale" — Add still gets the real default via PropertyContentFields.
    listing_type: Optional[Literal["Sale", "Rent"]] = None


@router.get("", response_model=list[PropertyRecord])
def get_properties(request: Request, limit: int = 100) -> Any:
    """Conditional (see Middleware/http_cache.py): a browser that already
    holds the current list — a reload, a second tab, a page revisit — gets a
    bodyless 304 instead of the whole list again. The version it is checked
    against comes from memory, so proving nothing changed costs no database
    work either.

    `limit` is clamped to LIST_MAX_LIMIT above rather than trusted: this is
    the endpoint every list page polls, and an unbounded number here would
    let one request ask this process to serialise anything at all.

    The time-format setting is part of the validator because it is part of
    the answer — every row carries an already-formatted timestamp (see
    property_pipeline_service._to_record), so flipping 12h/24h changes this
    response without changing a single property. Leaving it out pinned the
    old formatting in every open browser until something else happened to
    change a property.

    Returns the rendered bytes directly, from the cache above, instead of
    handing FastAPI a list of models to re-validate and re-serialise on
    every request. response_model is kept for the API schema; a route that
    returns a Response hands it straight to the client, which is the same
    thing the 304 path here has always done."""
    limit = max(1, min(limit, LIST_MAX_LIMIT))
    etag = http_cache.build_etag(
        "properties",
        limit,
        property_pipeline_service.get_properties_version(),
        display_settings_service.get_use_24_hour_format(),
    )
    if http_cache.is_unchanged(request, etag):
        return http_cache.not_modified(etag)
    body = _list_body(etag, limit)
    rendered = Response(content=body, media_type="application/json")
    http_cache.mark(rendered, etag)
    return rendered


def _list_body(etag: str, limit: int) -> bytes:
    """The rendered list for this exact version, built at most once.

    Two requests arriving together on a version nobody has rendered yet will
    both build it — deliberately, rather than holding the lock across the
    serialisation and making every other request queue behind it. Building
    it twice costs a little CPU once; serialising under a global lock would
    serialise the whole endpoint."""
    cached = _list_bodies.get(etag)
    if cached is not None:
        return cached
    body = _LIST_ADAPTER.dump_json(property_pipeline_service.get_properties(limit=limit))
    with _list_bodies_lock:
        _list_bodies[etag] = body
        while len(_list_bodies) > _LIST_BODY_MAX_ENTRIES:
            # Oldest insertion first — dicts preserve insertion order, and
            # the oldest entry is the one whose version is furthest behind.
            _list_bodies.pop(next(iter(_list_bodies)))
    return body


class PropertyImages(BaseModel):
    image_urls: List[str]


@router.get("/{record_id}", response_model=PropertyRecord)
def get_property(record_id: str, request: Request, response: Response) -> Any:
    """One property's full content — served from the in-memory snapshot, so
    this costs no database query. `image_urls` is always empty here and
    `image_count` carries the real number; the photos themselves come from
    the endpoint below.

    Tagged per property, not with the list-wide version: editing some other
    property must not invalidate this one in every open browser."""
    etag = _property_etag("property", record_id)
    unchanged = http_cache.conditional(request, response, etag)
    if unchanged is not None:
        return unchanged
    record = property_pipeline_service.get_property(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return record


@router.get("/{record_id}/images", response_model=PropertyImages)
def get_property_images(record_id: str, request: Request, response: Response) -> Any:
    """This property's photos — the only endpoint in the application that
    moves image data out of the database, and it runs only when someone
    presses Show photos on this specific property.

    The most valuable conditional request in the application: photos are
    base64 and routinely run to megabytes per property, they are looked at
    repeatedly, and they change only when someone actually edits that
    property. Every view after the first costs about a hundred bytes and no
    database read — instead of the entire payload, again."""
    etag = _property_etag("images", record_id)
    unchanged = http_cache.conditional(request, response, etag)
    if unchanged is not None:
        return unchanged
    images = property_pipeline_service.get_property_images(record_id)
    if images is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return PropertyImages(image_urls=images)


def _property_etag(scope: str, record_id: str) -> Optional[str]:
    """None for a property with no known version — see
    property_vector_store.get_property_version for why that must mean "do
    not cache" rather than "make something up". `scope` keeps the two
    endpoints' tags distinct, so a cached photo list can never be matched
    against the property-detail request for the same id."""
    version = property_pipeline_service.get_property_version(record_id)
    return None if version is None else http_cache.build_etag(scope, record_id, version)


@router.post("", response_model=PropertyRecord, status_code=201)
def create_property(body: PropertyContentFields) -> PropertyRecord:
    return property_pipeline_service.create_property(
        field_validation.bridge_contact_phones(body.model_dump())
    )


@router.patch("/{record_id}", response_model=PropertyRecord)
def update_property(record_id: str, body: PropertyUpdateRequest) -> PropertyRecord:
    # bridge_contact_phones BEFORE exclude_unset is read for anything
    # else: it both canonicalises the numbers a current browser sends and
    # folds an old browser's single contact_phone into them, without ever
    # introducing the key when this PATCH mentioned neither (which is what
    # keeps Accept/Move/Send from blanking a property's numbers).
    sent = field_validation.bridge_contact_phones(body.model_dump(exclude_unset=True))
    review_status = sent.pop("review_status", None)
    needs_review = sent.pop("needs_review", None)
    landing_page = sent.pop("landing_page", None)
    updated = property_pipeline_service.update_property(
        record_id,
        review_status=review_status,
        needs_review=needs_review,
        content_updates=sent or None,
        landing_page=landing_page,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return updated


@router.delete("/{record_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_property(record_id: str) -> None:
    deleted = property_pipeline_service.delete_property(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Property not found")
