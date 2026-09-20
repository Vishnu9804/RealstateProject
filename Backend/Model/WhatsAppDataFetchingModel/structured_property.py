import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from Model import phone_numbers
from Model.record_source import SOURCE_WHATSAPP


class StructuredProperty(BaseModel):
    """A single property listing, structured from a raw WhatsApp message by
    the LLM stage (Agent/WhatsAppDataFetchingAgent/property_structurer.py) and merged with the
    WhatsApp metadata that was already known for certain (sender/group/
    timestamp) rather than re-derived by the LLM. This is the shape the
    Postgres properties table (later step) will mirror.
    """

    # A single WhatsApp message can now yield MORE THAN ONE property (see
    # property_structurer.py's PART 2), so source_message_id — which
    # identifies the MESSAGE, not the property — is no longer unique per
    # record: two/three properties pulled from the same message legitimately
    # share it. record_id is the one field guaranteed unique per PROPERTY,
    # generated once here at creation and never regenerated afterwards (the
    # DB round-trip and the in-memory store both preserve it as-is) — this
    # is what the frontend must use as its row key/identity, not
    # source_message_id.
    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    source_message_id: str

    # WHERE this property came from — one of Model/record_source.py's
    # PROPERTY_SOURCES, as a plain string (never an enum, see that module).
    #
    # Defaulted to "whatsapp" because the LLM structuring stage is the one
    # place a StructuredProperty is built without an explicit answer being
    # interesting: that stage exists only to turn WhatsApp messages into
    # properties. It is nonetheless passed explicitly there too
    # (property_structurer._build_property), so nothing about a stored
    # property's origin is implicit. Every other writer — the Properties
    # page's Add dialog, any future spreadsheet import — MUST pass its own
    # value; forgetting is the one failure mode this default cannot catch,
    # which is why the constants live in one module rather than being typed
    # at each call site.
    #
    # Provenance, not content: deliberately absent from
    # Database/property_repository.py's EDITABLE_CONTENT_FIELDS, so no Edit
    # dialog save can ever rewrite where a property came from.
    source: str = SOURCE_WHATSAPP

    # --- extracted by the LLM from the message text ---
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    # The individual unit's own number within its building — "402", "A-404",
    # "Shop 12". The client's two spreadsheets call this "unit_no" and
    # "flat_no" respectively; it is one field with one meaning, shown as
    # "Unit / Flat number" everywhere in the UI.
    unit_no: Optional[str] = None
    society_name: Optional[str] = None  # building/project/society name, e.g. "Black Residency" — distinct
    # from area_name (the broader locality, e.g. "Althan") and address (other address details).
    # The client's two spreadsheets call this "society_name" and
    # "building_name"; same field, shown as "Society / Building name".
    area_name: Optional[str] = None
    address: Optional[str] = None
    # The property's area, in whichever of the two units the listing actually
    # used — square feet and vaar (= gaj = square yard) are the only two the
    # client records, and they are deliberately SEPARATE columns rather than
    # one number plus a unit label. A number is only ever comparable to
    # another number in the same unit, and keeping them apart means nothing
    # can read one as the other. Normally exactly one is filled; both may be
    # when the listing itself quoted both. No conversion is ever performed at
    # write time (see Service/ClientPropertyMatchingService/normalization.py's
    # property_area_sqft, which converts only for scoring).
    area_sqft: Optional[float] = None
    area_vaar: Optional[float] = None
    # The "super built" (super built-up) area, exactly as a human typed it in
    # the Add/Edit dialog — e.g. "1850 sq ft". Never extracted by the LLM: it
    # is deliberately absent from the extraction schema
    # (Agent/WhatsAppDataFetchingAgent/glm_extraction_schema.py) and the
    # structuring prompt, so it adds nothing to any GLM call. Free text rather
    # than a number because it is quoted with its own unit and wording.
    super_built: Optional[str] = None
    # "Unfurnished" | "Semi furnished" | "Fully furnished", or None when the
    # listing never said. Free-form str rather than an enum because the LLM
    # is asked to normalize onto those three and a stored value that somehow
    # isn't one of them must still round-trip rather than fail validation.
    furnishing: Optional[str] = None
    # THE TOTAL price of the property. There is deliberately no per-unit rate
    # column: a rate quoted per sqft/vaar is used during structuring to
    # DERIVE this total (see property_structurer._fill_missing_price) and is
    # then discarded — the client tracks one price per property, and a stored
    # rate that could be mistaken for a total is worse than no rate at all.
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    # "Sale" vs "Rent", classified by the LLM (see property_structurer.py's
    # RENT VS SALE CLASSIFICATION rules). Defaults to "Sale" whenever the
    # message gives no explicit Rent/Sale signal — the safe default per
    # product decision, so a listing never lands in the Rent bucket without
    # an explicit signal earning it.
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    # EVERY contact number on this listing, each one canonical "+91" plus 10
    # digits — see Model/phone_numbers.py, which is the only thing that
    # produces a value for this field. A listing routinely carries two or
    # three numbers (an owner and a broker, or a number and a WhatsApp-only
    # number), and before this they all had to share one box: a message that
    # said "98765 43210 / 98765 43211" was stored as that whole string, and
    # the client's spreadsheet had gone further still and run two numbers
    # together into one unusable 20-digit run.
    #
    # THE ONLY contact-number field on this model. There used to be a
    # derived `contact_phone` scalar beside it holding contact_phones[0];
    # it is gone, from the model, from every API response and from the
    # database. One number has one home. The two readers that genuinely
    # want a single string ask for it explicitly now --
    # phone_numbers.primary_phone(prop.contact_phones) -- which is what the
    # scalar always was, so the embedding text those readers build is
    # byte-for-byte what it was before and stays comparable with every
    # vector already stored (see Service/WhatsAppDataFetchingService/
    # embedding_service.py's EMBEDDING_TEXT_FIELDS).
    contact_phones: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    # --- set by a human on the Properties page, never by the LLM ---
    # The Instagram reel this property was posted as, if any — how the
    # InstagramInquiryHandling pipeline matches a reel comment/DM share back
    # to the property it's about (see Service/InstagramInquiryHandlingService).
    instagram_reel_url: Optional[str] = None
    # Photos of the property, set by a human on the Properties page — never
    # by the LLM (same reasoning as instagram_reel_url: nothing in the raw
    # WhatsApp text is a photo for it to extract). Each entry is a data URL
    # (already resized/compressed client-side before upload), stored in the
    # order the user arranged them; the first is the cover photo. Optional —
    # an empty list is the common case, not an error.
    image_urls: List[str] = Field(default_factory=list)
    # A map/pin link to where the property actually is, pasted by a human from
    # the client's own records.
    #
    # INTERNAL ONLY, AND THAT IS ENFORCED BY CONSTRUCTION, NOT BY CARE: it is
    # absent from every outbound shape there is — the public landing-page
    # models (Model/LandingPageModel/landing_property.py), the matched-property
    # shape the WhatsApp share and agent hand-off messages are built from
    # (Model/ClientPropertyMatchingModel/matched_property.py), and the
    # Instagram auto-reply template (Service/InstagramInquiryHandlingService/
    # instagram_message_templates.py). A pin is the one field that lets
    # someone reach a property without the broker, so it must never travel to
    # a client, a visitor, a broker or an agent. If you add it to any model
    # that leaves this building, you have broken that guarantee.
    location_url: Optional[str] = None
    # Whether a video of this property exists (the client's spreadsheet
    # records it as a yes/no, not a link). Human-set, never extracted.
    video_available: bool = False
    # Whatever else the client noted about this property in their own
    # spreadsheet's "Extra" column. Human-only free text — deliberately not
    # written by the LLM, which has `description` for its own summary.
    extra_notes: Optional[str] = None
    # The client's "AVL or Not" toggle: is this property still on offer?
    # Defaults to True — a property is available unless someone says
    # otherwise. Deliberately NOT the same thing as the Sold out tab, which
    # moves a closed deal out of the table entirely (see
    # Database/soldout_property_models.py); this is the softer "off the market
    # for now" flag the client already keeps by hand.
    is_available: bool = True

    # --- known for certain from WhatsApp itself, not from the LLM ---
    group_name: str
    chat_type: Literal["group", "personal"]
    sender_name: str
    sender_saved_name: str
    sender_phone: str
    message_text: str
    message_timestamp: datetime

    # --- "accepted" vs "outsider" is decided once, by the LLM structuring
    # stage (see property_structurer.py), based on whether the property
    # falls inside a client-selected area. This is the property's permanent
    # home tab (Main vs Outsider) and is never changed by review — a human
    # can still move a property between the two later (see
    # property_pipeline_service.update_property). ---
    review_status: Literal["accepted", "outsider"] = "accepted"

    # --- independent of review_status: set True by the LLM structuring
    # stage (see property_structurer.py's PART 4) for a property it could
    # barely extract anything from — a fragment with almost no usable
    # information, not merely one with a field or two missing. The bar is
    # deliberately extreme, and enforced twice: the prompt sets it, and
    # _apply_information_review re-checks it deterministically against what
    # was actually extracted, so this queue stays tiny by construction.
    #
    # A flagged property is excluded from client-property matching entirely
    # (see Service/ClientPropertyMatchingService/matching_service.py) —
    # there is nothing in it to match on. It exists only so a human can
    # read the relevant part of the original message (carried in
    # `description`, see property_structurer._apply_information_review),
    # fill the details in by hand, and file it into Main or Outsider.
    # Cleared back to False at that point, after which the property behaves
    # exactly like any other. ---
    needs_review: bool = False
    review_notes: Optional[str] = None

    # --- the Landing Page page's own state — never set by the LLM, and not
    # part of the Add/Edit dialog either (see PropertyContentFields): these
    # three are managed entirely by property_pipeline_service.update_property
    # and property_pipeline_service.create_property. ---
    # Whether this property is currently published to the public landing
    # page. Only ever True for a property that has at least one photo or an
    # Instagram reel — the Landing Page page is the only place this is set.
    on_landing_page: bool = False
    # When on_landing_page was last flipped, either direction — what Live's
    # "newest sent first" ordering sorts by, and what Ready to Add's "removed
    # recently sinks back down" behavior relies on simply NOT touching (a
    # property that got removed keeps whatever qualified_at it already had,
    # rather than jumping back to the top of Ready to Add).
    landing_page_updated_at: Optional[datetime] = None
    # When this property most recently gained a photo or an Instagram reel —
    # what Ready to Add's "just qualified rises to the top" ordering sorts
    # by. Set whenever an edit adds photos or a reel link, never touched by
    # the Landing Page page's own Send/Remove actions.
    qualified_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _reconcile_contact_phones(cls, data):
        """Makes contact_phones canonical, whichever shape the caller
        supplied it in.

        Three kinds of caller reach this, and each needs a different half:

        - A caller with ONE free-text string straight out of a message
          ("98765 43210 / 98765 43211") passes it as the inbound-only
          `contact_phone` alias, which is split here into the numbers it
          holds and then dropped. The alias is read but never emitted: it
          is not a field on this model and never appears in a model_dump()
          or an API response.
        - The Add/Edit dialog and the one-time migration pass
          contact_phones, already canonical. The all-canonical fast path in
          normalize_phone_list means that costs a handful of character
          comparisons and no rebuilding at all.
        - Every read out of Postgres passes contact_phones too (it is the
          stored column), so this runs on every property in the in-memory
          snapshot -- hence the care above about it being cheap.

        WHY "before" AND NOT "after", WHICH READS MORE NATURALLY

        Only the raw input can tell "no list was given, read the scalar"
        apart from "an EMPTY list was given, meaning the numbers were
        cleared" -- by the time the model exists both look like []. That
        distinction is the whole correctness of clearing a listing's
        numbers: a model rebuilt from its own model_dump() (which
        property_pipeline_service.update_property and _to_record both do)
        would have read a stale scalar back and resurrected a number that
        had just been deleted.

        So: a contact_phones key that is present and not None always wins,
        even when it is empty. The `contact_phone` alias is read only when
        there is no list at all."""
        if not isinstance(data, dict):
            return data
        merged = {key: value for key, value in data.items() if key != "contact_phone"}
        supplied = merged.get("contact_phones")
        if supplied is not None:
            merged["contact_phones"] = phone_numbers.normalize_phone_list(supplied)
        else:
            merged["contact_phones"] = phone_numbers.split_phone_numbers(data.get("contact_phone"))
        return merged
