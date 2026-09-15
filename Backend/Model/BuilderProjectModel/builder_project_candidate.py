from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

# What a matched listing is — the value every match surface carries as
# `property_source` (Model/ClientPropertyMatchingModel/matched_property.py,
# and the agent visit snapshots in Model/AgentManagementModel/).
PROPERTY_SOURCE = "property"
BUILDER_PROJECT_SOURCE = "builder_project"

# A builder project has no WhatsApp message behind it, so the message fields
# an EmbeddedProperty requires get these fixed, clearly-not-a-chat values.
# Nothing on the match path reads them (scoring and the matched-property
# display fields never do) — they exist only so the object is complete.
_BUILDER_PROJECT_LABEL = "Builder project"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class BuilderProjectCandidate(EmbeddedProperty):
    """A builder project in the exact shape the matching engine scores —
    so builder projects are matched against client inquiries and broker
    requirements by the SAME, unmodified code a property is (Service/
    ClientPropertyMatchingService/scoring.py): same critical gate, same
    budget/location/BHK/size curves, same semantic comparison of vectors
    built from the same kind of text with the same model.

    Its own class, not a flag on EmbeddedProperty, so "is this a builder
    project?" is an isinstance check (see Service/ClientPropertyMatchingService/
    match_candidates.py) and nothing about a property's shape, its API record
    or its table changes.

    Always "accepted" and never flagged for review: a builder project is
    typed in by a person who knows what it is, so it is always matchable,
    and it is filed with the Main listings in the match dialogs.

    Built once per cached project (Service/BuilderProjectService/
    builder_project_store.py's BuilderProjectEntry.candidate) with
    model_construct — the values come straight from the project's own
    validated columns, so re-validating them would only cost CPU, and
    construct lets the candidate share the cached vector instead of copying
    it."""

    @classmethod
    def from_fields(cls, fields: Mapping[str, Any], embedding: Optional[Any]) -> "BuilderProjectCandidate":
        record_id = fields["record_id"]
        timestamp = fields.get("created_at") or fields.get("updated_at") or _EPOCH
        return cls.model_construct(
            record_id=record_id,
            source_message_id=f"builder-project-{record_id}",
            property_type=fields.get("property_type"),
            bhk=fields.get("bhk"),
            unit_no=fields.get("unit_no"),
            society_name=fields.get("society_name"),
            area_name=fields.get("area_name"),
            address=fields.get("address"),
            area_sqft=fields.get("area_sqft"),
            area_vaar=fields.get("area_vaar"),
            super_built=fields.get("super_built"),
            furnishing=fields.get("furnishing"),
            price_text=fields.get("price_text"),
            price_amount_inr=fields.get("price_amount_inr"),
            listing_type=fields.get("listing_type") or "Sale",
            contact_name=fields.get("contact_name"),
            contact_phone=fields.get("contact_phone"),
            description=fields.get("description"),
            instagram_reel_url=fields.get("instagram_reel_url"),
            # Photos never travel through matching — see the property
            # snapshot's own rule (Service/WhatsAppDataFetchingService/
            # property_snapshot.py). Sharing a matched builder project's
            # photos reads them on demand (property_share_service).
            image_urls=[],
            location_url=fields.get("location_url"),
            video_available=bool(fields.get("video_available")),
            extra_notes=fields.get("extra_notes"),
            is_available=fields.get("is_available", True) is not False,
            group_name=_BUILDER_PROJECT_LABEL,
            chat_type="personal",
            sender_name=_BUILDER_PROJECT_LABEL,
            sender_saved_name=_BUILDER_PROJECT_LABEL,
            sender_phone="",
            message_text="",
            message_timestamp=timestamp,
            review_status="accepted",
            needs_review=False,
            review_notes=None,
            on_landing_page=False,
            landing_page_updated_at=None,
            qualified_at=None,
            # None when the project has no vector yet — scoring then treats
            # the semantic field as "not comparable", never as a miss.
            embedding=embedding,
            embedding_model="",
        )
