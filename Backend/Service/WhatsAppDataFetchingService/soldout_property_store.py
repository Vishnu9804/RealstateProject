"""Storage abstraction for sold-out properties — the one place
soldout_property_service.py goes to read/write them. Callers never know or
care which backend is active underneath:

  - DATABASE_URL unset: an in-memory list, exactly like the fallback
    Service/WhatsAppDataFetchingService/property_vector_store.py has always
    had for live properties.
  - DATABASE_URL set: delegates to Database/soldout_property_repository.py.

The move itself (a property leaving `properties` and arriving here) is a
single operation on purpose rather than "add here, then delete there" done
by the caller — see move_property_to_soldout below.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Set, Tuple

from Database import soldout_property_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.soldout_property import SoldOutProperty
from Service.WhatsAppDataFetchingService import property_vector_store

# In-memory fallback only — untouched whenever a database is configured.
_soldout: List[SoldOutProperty] = []


def move_property_to_soldout(prop: SoldOutProperty) -> bool:
    """Records this property as sold out and removes it from the live
    property store, as one indivisible step.

    In database mode that is literally one transaction (see
    soldout_property_repository.move_property_to_soldout) — there is no
    moment at which a property exists in both tables, or in neither.

    Returns False when this property was already sold out; the caller
    treats that as "nothing to do", not as an error, so the action stays
    safe to retry or double-click.
    """
    if is_database_configured():
        return soldout_property_repository.move_property_to_soldout(prop)
    if any(existing.record_id == prop.record_id for existing in _soldout):
        property_vector_store.delete_property(prop.record_id)
        return False
    _soldout.append(prop)
    property_vector_store.delete_property(prop.record_id)
    return True


def get_all(limit: int = 500) -> List[SoldOutProperty]:
    if is_database_configured():
        return soldout_property_repository.get_all(limit)
    return sorted(_soldout, key=lambda prop: prop.sold_out_at, reverse=True)[:limit]


def get_all_summary(limit: int = 500) -> List[Tuple[SoldOutProperty, int]]:
    """Same rows as get_all, paired with each one's photo count, without the
    Postgres implementation ever loading the photo bytes — see
    Database/soldout_property_repository.py's own version of this. The
    in-memory fallback already holds everything in RAM, so counting is free
    there either way."""
    if is_database_configured():
        return soldout_property_repository.get_all_summary(limit)
    return [(prop, len(prop.image_urls)) for prop in get_all(limit)]


def get(record_id: str) -> Optional[SoldOutProperty]:
    if is_database_configured():
        return soldout_property_repository.get(record_id)
    return next((prop for prop in _soldout if prop.record_id == record_id), None)


def get_count() -> int:
    if is_database_configured():
        return soldout_property_repository.get_count()
    return len(_soldout)


def delete(record_id: str) -> bool:
    if is_database_configured():
        return soldout_property_repository.delete(record_id)
    for index, prop in enumerate(_soldout):
        if prop.record_id == record_id:
            del _soldout[index]
            return True
    return False


def filter_soldout_record_ids(record_ids: Sequence[str]) -> Set[str]:
    """Which of these property ids are sold out — see
    Database/soldout_property_repository.py's own version for why this is
    shaped as "filter the ids I already hold" rather than "give me every
    sold-out id"."""
    ids = list(record_ids)
    if not ids:
        return set()
    if is_database_configured():
        return soldout_property_repository.filter_soldout_record_ids(ids)
    wanted = set(ids)
    return {prop.record_id for prop in _soldout if prop.record_id in wanted}
