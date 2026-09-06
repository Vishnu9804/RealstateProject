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


def build_requirement_text(client: ClientRecord) -> str:
    parts = [
        client.purpose,
        client.property_type,
        client.bhk,
        _budget_text(client.budget_min_inr, client.budget_max_inr),
        client.preferred_areas,
        client.additional_requirements,
    ]
    return " | ".join(part for part in parts if part)


def _budget_text(budget_min: Optional[float], budget_max: Optional[float]) -> str:
    if budget_min is None and budget_max is None:
        return ""
    if budget_min is not None and budget_max is not None:
        return f"budget {budget_min:.0f} to {budget_max:.0f}"
    if budget_min is not None:
        return f"budget above {budget_min:.0f}"
    return f"budget up to {budget_max:.0f}"
