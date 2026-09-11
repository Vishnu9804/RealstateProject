"""HTTP routes for sold-out properties — the Properties page's Sold out tab,
and the action that puts a property there. Thin by design; everything the
move actually does lives in Service/WhatsAppDataFetchingService/
soldout_property_service.py.
"""

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from Middleware import http_cache
from Model.WhatsAppDataFetchingModel.soldout_property import SoldOutPropertyRecord
from Service.WhatsAppDataFetchingService import soldout_property_service, soldout_property_store

router = APIRouter(prefix="/soldout-properties", tags=["soldout-properties"])


class SoldOutPropertyImages(BaseModel):
    image_urls: List[str]


class SoldOutActionResult(BaseModel):
    """What marking a property sold out actually did. `agents_notified`
    counts agents REACHED on WhatsApp, so a send that failed is visible to
    the operator rather than silently reported as done — the same honesty
    the existing cancellation endpoint's CancelResult has."""

    property: SoldOutPropertyRecord
    visits_cancelled: int
    agents_notified: int
    agents_failed: int


@router.get("", response_model=list[SoldOutPropertyRecord])
def get_soldout_properties(request: Request, response: Response, limit: int = 500) -> Any:
    """The Sold out tab's list, newest sale first.

    Served from the in-memory cache, so this costs no database query (see
    soldout_property_store's own docstring), and conditional on top of that:
    a browser that already holds the current list gets a bodyless 304. Since
    a sold-out record is never edited, that tag only changes when a NEW sale
    is recorded — which for this tab means "almost never re-transferred".
    """
    etag = http_cache.build_etag("soldout-properties", limit, soldout_property_service.get_sold_out_version())
    unchanged = http_cache.conditional(request, response, etag)
    return unchanged if unchanged is not None else soldout_property_service.get_sold_out_properties(limit=limit)


@router.get("/{record_id}/images", response_model=SoldOutPropertyImages)
def get_soldout_property_images(record_id: str, request: Request, response: Response) -> Any:
    """This sold-out property's photos — like the properties endpoint it
    mirrors, the only place image data for these records leaves the
    database, and only when someone presses Show photos on this one record.

    Tagged with the row's own `sold_out_at`, which never changes: a
    sold-out record is immutable, so once a browser has these photos it
    never has to download them again."""
    etag = _images_etag(record_id)
    unchanged = http_cache.conditional(request, response, etag)
    if unchanged is not None:
        return unchanged
    images = soldout_property_service.get_sold_out_property_images(record_id)
    if images is None:
        raise HTTPException(status_code=404, detail="Sold-out property not found")
    return SoldOutPropertyImages(image_urls=images)


def _images_etag(record_id: str) -> Optional[str]:
    """None for a record the in-memory cache doesn't hold, which means "do
    not cache" rather than "make something up" — see
    Middleware/http_cache.conditional for why that is the only safe
    response to a validator that can't be trusted."""
    entry = soldout_property_store.get(record_id)
    return None if entry is None else http_cache.build_etag("soldout-images", record_id, entry.sold_out_at)


@router.post("/{record_id}", response_model=SoldOutActionResult, status_code=201)
def mark_property_sold_out(record_id: str) -> SoldOutActionResult:
    """The Properties page's "Move to → Sold out" action.

    Moves the property out of the property database into the sold-out
    table, removes every reference that must not outlive it (cached match
    scores, hand-picked shortlists, pending site visits) and tells each
    agent who had a pending visit — once, however many visits they held.
    See the service's own docstring for the full story.
    """
    result = soldout_property_service.mark_sold_out(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return SoldOutActionResult(
        property=result.property,
        visits_cancelled=result.visits_cancelled,
        agents_notified=result.agents_notified,
        agents_failed=result.agents_failed,
    )
