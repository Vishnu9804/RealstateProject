"""HTTP routes for sold-out properties — the Properties page's "Move to →
Sold out" action, and the Sold out view that reads the result back. Thin by
design; all the logic (and everything that has to be unwound when a deal
closes) lives in Service/WhatsAppDataFetchingService/soldout_property_service.py.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.soldout_property import SoldOutMoveResult, SoldOutPropertyRecord
from Service.WhatsAppDataFetchingService import soldout_property_service

router = APIRouter(prefix="/soldout-properties", tags=["soldout-properties"])


class MarkSoldOutRequest(BaseModel):
    """The live property whose deal has closed. Only its id: everything else
    about the listing is read from the stored property, never trusted from
    the browser — the snapshot that lands in `soldout_properties` has to be
    what was actually on file."""

    record_id: str


@router.get("", response_model=list[SoldOutPropertyRecord])
def get_soldout_properties(limit: int = 500) -> list[SoldOutPropertyRecord]:
    """Newest sale first. Deliberately photo-less, exactly like
    GET /properties — `image_count` is accurate, `image_urls` is empty; use
    the single-record route below for the real photos."""
    return soldout_property_service.get_soldout_properties(limit=limit)


@router.get("/{record_id}", response_model=SoldOutPropertyRecord)
def get_soldout_property(record_id: str) -> SoldOutPropertyRecord:
    record = soldout_property_service.get_soldout_property(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Sold-out property not found")
    return record


@router.post("", response_model=SoldOutMoveResult, status_code=201)
def mark_property_sold_out(body: MarkSoldOutRequest) -> SoldOutMoveResult:
    """Moves one live property out of `properties` and into
    `soldout_properties`, cancels every active site visit against it
    (messaging each agent involved), and drops the cached match scores and
    hand-picks that pointed at it. See the service module's own docstring
    for why this is a move rather than a flag, and for what is deliberately
    NOT touched (completed visits).

    404 when there is no such live property — which is also what a second
    click gets, since the first one already moved it."""
    result = soldout_property_service.mark_property_sold_out(body.record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return result


@router.delete("/{record_id}", status_code=204)
def delete_soldout_property(record_id: str) -> None:
    """Erases one sold-out record for good. There is deliberately no route
    back to the live table — see soldout_property_service.delete_soldout_property."""
    deleted = soldout_property_service.delete_soldout_property(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sold-out property not found")
