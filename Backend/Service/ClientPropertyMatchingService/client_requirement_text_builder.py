"""Builds the canonical text embedded into a client's `requirement_embedding`
column (Database/client_models.py). Same treatment
Service/WhatsAppDataFetchingService/embedding_service.py's
build_embedding_text() gives a property, applied to a client's requirements
instead, so both vectors are built the same way and land in the same
semantic space — a meaningful cosine similarity between them depends on
that, not just on using the same model.
"""

from __future__ import annotations

from typing import Optional

from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import normalization


def build_requirement_text(client: ClientRecord) -> str:
    parts = [
        client.purpose,
        _property_type_text(client),
        client.bhk,
        _budget_text(client.budget_min_inr, client.budget_max_inr),
        client.preferred_areas,
        # Only ever present for someone who actually picked one, so every
        # client and every broker requirement stored before this field
        # existed produces byte-for-byte the text it always did — which
        # matters twice over: their vector is unchanged, and so is the
        # fingerprint a broker requirement's stored matches are keyed on
        # (requirement_matching_service._fingerprint).
        client.furnishing,
        client.additional_requirements,
    ]
    return " | ".join(part for part in parts if part)


def _property_type_text(client: ClientRecord) -> Optional[str]:
    """property_type exactly as stored — unless the client gave a size for
    any of their types, in which case each size is written beside its type
    ("Flat 1200 sqft, Bungalow 200 vaar") so the vector carries it too.

    Only a client with sizes gets different text. Every other client, and
    every broker requirement (whose change-detection fingerprint is a hash
    of this text — see requirement_matching_service._fingerprint), produces
    byte-for-byte the text it always did."""
    if not client.property_sizes or not client.property_type:
        return client.property_type
    return ", ".join(
        f"{group} {size}" if (size := normalization.size_for(client.property_sizes, group)) else group
        for group in normalization.split_type_groups(client.property_type)
    )


def _budget_text(budget_min: Optional[float], budget_max: Optional[float]) -> str:
    if budget_min is None and budget_max is None:
        return ""
    if budget_min is not None and budget_max is not None:
        return f"budget {budget_min:.0f} to {budget_max:.0f}"
    if budget_min is not None:
        return f"budget above {budget_min:.0f}"
    return f"budget up to {budget_max:.0f}"
