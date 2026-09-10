"""Postgres implementation of the sold-out property store — the production
backend behind Service/WhatsAppDataFetchingService/soldout_property_store.py
once DATABASE_URL is set. Callers never call this module directly.

Shaped after Database/property_repository.py on purpose: same summary-vs-full
split (a list view never ships photo bytes), same `_COLUMNS` mapping style,
same "convert to the pydantic model at the boundary" contract.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from Database.models import PropertyRow
from Database.session import get_session
from Database.soldout_property_models import SoldOutPropertyRow
from Model.WhatsAppDataFetchingModel.soldout_property import SoldOutProperty

_COLUMNS = (
    "record_id",
    "source_message_id",
    "property_type",
    "bhk",
    "society_name",
    "area_name",
    "address",
    "carpet_area_sqft",
    "carpet_area_unit",
    "price_text",
    "price_amount_inr",
    "price_per_unit_text",
    "price_per_unit_amount_inr",
    "listing_type",
    "contact_name",
    "contact_phone",
    "description",
    "instagram_reel_url",
    "image_urls",
    "group_name",
    "chat_type",
    "sender_name",
    "sender_saved_name",
    "sender_phone",
    "message_text",
    "message_timestamp",
    "review_status",
    "needs_review",
    "review_notes",
    "on_landing_page",
    "landing_page_updated_at",
    "qualified_at",
    "sold_out_at",
)

_SUMMARY_COLUMNS = tuple(name for name in _COLUMNS if name != "image_urls")


def move_property_to_soldout(prop: SoldOutProperty) -> bool:
    """Inserts this property into `soldout_properties` AND deletes the
    matching `properties` row, in ONE transaction.

    Both halves in one session is the whole point: the two tables live on
    the same engine (see Database/session.py), so a single commit means
    there is no instant at which a property is in both places, and no way
    for a failure between the two steps to either lose a listing or leave a
    ghost behind. Anything that raises rolls the whole thing back and the
    property is simply still for sale.

    Returns False (having changed nothing) if this record_id has already
    been sold out — the action is idempotent, so a double-click or a retry
    is a no-op rather than a duplicate row or a crash.
    """
    with get_session() as session:
        already = session.execute(
            select(SoldOutPropertyRow.id).where(SoldOutPropertyRow.record_id == prop.record_id)
        ).first()
        if already is not None:
            # Still make sure the live row is gone — a previous attempt
            # that died between the insert and the delete (impossible now,
            # but a database can be edited by hand) must not leave the
            # property visible in both places forever.
            _delete_property_row(session, prop.record_id)
            return False
        session.add(SoldOutPropertyRow(**{name: getattr(prop, name) for name in _COLUMNS}))
        _delete_property_row(session, prop.record_id)
        return True


def _delete_property_row(session: Session, record_id: str) -> None:
    """Removes the live `properties` row for this record_id, if it's still
    there. Mirrors property_repository._find_row's "legacy-{id}" fallback so
    a row written before record_id existed can be sold out too — see that
    function's own comment."""
    row: Optional[PropertyRow] = None
    if record_id.startswith("legacy-"):
        try:
            row = session.get(PropertyRow, int(record_id[len("legacy-") :]))
        except ValueError:
            row = None
    else:
        row = session.execute(select(PropertyRow).where(PropertyRow.record_id == record_id)).scalar_one_or_none()
    if row is not None:
        session.delete(row)


def get_all(limit: int) -> List[SoldOutProperty]:
    stmt = select(SoldOutPropertyRow).order_by(SoldOutPropertyRow.sold_out_at.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_all_summary(limit: int) -> List[Tuple[SoldOutProperty, int]]:
    """Newest sale first, minus the contents of `image_urls` — same trick,
    for the same reason, as property_repository.get_all_properties_summary:
    that one column can hold megabytes of base64 photo data per row, and a
    list view only ever needs the count, which is computed in SQL here."""
    stmt = (
        select(SoldOutPropertyRow, func.json_array_length(SoldOutPropertyRow.image_urls).label("image_count"))
        .options(defer(SoldOutPropertyRow.image_urls))
        .order_by(SoldOutPropertyRow.sold_out_at.desc())
        .limit(limit)
    )
    with get_session() as session:
        rows = [(row[0], row[1]) for row in session.execute(stmt).all()]
    return [(_to_pydantic_summary(row), count) for row, count in rows]


def get(record_id: str) -> Optional[SoldOutProperty]:
    stmt = select(SoldOutPropertyRow).where(SoldOutPropertyRow.record_id == record_id)
    with get_session() as session:
        row = session.execute(stmt).scalar_one_or_none()
        return _to_pydantic(row) if row is not None else None


def get_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(SoldOutPropertyRow)).scalar_one()


def delete(record_id: str) -> bool:
    with get_session() as session:
        row = session.execute(
            select(SoldOutPropertyRow).where(SoldOutPropertyRow.record_id == record_id)
        ).scalar_one_or_none()
        if row is None:
            return False
        session.delete(row)
        return True


def filter_soldout_record_ids(record_ids: Sequence[str]) -> Set[str]:
    """Which of these ids are sold out — one indexed `IN` lookup returning
    ids only, never a row. Lets a caller holding a handful of property ids
    (a client's website enquiries, see Service/LandingPageService/
    landing_page_service.py's get_property_ids_for_phone) drop the sold ones
    without loading a single property. Empty input short-circuits, so the
    common case costs no query at all."""
    ids = list(record_ids)
    if not ids:
        return set()
    stmt = select(SoldOutPropertyRow.record_id).where(SoldOutPropertyRow.record_id.in_(ids))
    with get_session() as session:
        return set(session.execute(stmt).scalars().all())


def _to_pydantic(row: SoldOutPropertyRow) -> SoldOutProperty:
    return SoldOutProperty(**{name: getattr(row, name) for name in _COLUMNS})


def _to_pydantic_summary(row: SoldOutPropertyRow) -> SoldOutProperty:
    """Like _to_pydantic, but never touches the deferred `image_urls`
    attribute — reading it would fire one extra SELECT per row and defeat
    the point of deferring it in get_all_summary. The real count travels
    alongside as that function's own tuple element."""
    return SoldOutProperty(**{name: getattr(row, name) for name in _SUMMARY_COLUMNS}, image_urls=[])
