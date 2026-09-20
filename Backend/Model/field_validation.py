"""Deterministic checks for the fields a HUMAN types into the dashboard's
Add/Edit dialogs — one place, so the Properties page, the Builder Projects
page, the Broker Requirements page and the Agents page all refuse the same
nonsense with the same wording.

WHERE THIS IS USED, AND WHERE IT DELIBERATELY IS NOT

Only on the REQUEST models in Controller/ (PropertyContentFields,
RequirementCreateRequest, AgentCreateRequest, ...), never on the domain
models in Model/ that the LLM pipelines build. That distinction is
load-bearing: a StructuredProperty/StructuredRequirement is assembled from
whatever a broker happened to write in a WhatsApp message, and a validator
there would turn a badly-worded listing into a raised exception that loses
the whole batch. A human filling in a form, on the other hand, is right
there to correct it — so that is the only place anything is refused.

For the same reason nothing here REWRITES a free-text contact number: the
check is "does this contain a plausible number at all", not "is this one
canonical number". The one exception is an agent's own WhatsApp number
(`to_e164`), which is a single number this application actually SENDS to and
identifies an agent by, so it is normalized exactly like a client's is.

Every function takes the raw value and returns the value to store, raising
ValueError with a sentence meant to be read by the person who typed it (see
main.py's validation handler, which is what puts that sentence in front of
them instead of pydantic's own field dump).
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, field_validator

# The ceiling on any rupee amount a form may carry — ₹1 lakh crore. Far above
# any real property, and low enough that a stray "1e20" typed into a budget
# box cannot reach the card renderer (which faithfully printed it as
# "up to 10000000000000cr").
MAX_INR = 1e12

# The ceiling on an area figure, in sqft or vaar. Same reasoning as MAX_INR:
# generous enough for any real plot, finite enough that a typo is caught.
MAX_AREA = 1e7

# At least this many digits before a free-text contact field counts as
# holding a phone number at all. Deliberately a DIGIT COUNT and not a strict
# phone parse: this field legitimately holds things like
# "98765 43210 / 98765 43211" or a number with an extension, and refusing
# those would block edits to properties the LLM captured perfectly well.
# "abc123" (3 digits) is what this is here to catch.
_MIN_CONTACT_DIGITS = 7
_MAX_CONTACT_DIGITS = 40

# http(s) with something that looks like a host after it. Not a full URL
# parser — it only has to tell a real pasted link apart from "not a url".
_URL_RE = re.compile(r"^https?://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?$", re.IGNORECASE)

# The same shapes landing_page_service._REEL_URL_PATTERN and
# instagram_reel_matcher._REEL_CODE_RE already read a shortcode out of — a
# link that does not match is a link neither of them can do anything with, so
# it must not be storable as one.
_REEL_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)


def clean_text(value: Optional[str]) -> Optional[str]:
    """Trimmed, with blank/whitespace-only becoming None — so an emptied box
    actually clears the stored value rather than storing "" or "   "."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def check_contact_phone(value: Optional[str]) -> Optional[str]:
    """A free-text contact number as typed, kept as typed. Raises when it
    holds no plausible number at all — see _MIN_CONTACT_DIGITS for why this
    is a digit count rather than a phone parse."""
    trimmed = clean_text(value)
    if trimmed is None:
        return None
    digits = sum(1 for character in trimmed if character.isdigit())
    if digits < _MIN_CONTACT_DIGITS or digits > _MAX_CONTACT_DIGITS:
        raise ValueError("That doesn't look like a valid phone number.")
    return trimmed


def to_e164(value: Optional[str]) -> Optional[str]:
    """One real, single phone number, normalized to "+91..." — used only
    where the number IS an identity this application sends to (an agent's
    WhatsApp number). Raises when it is not a valid number.

    Imported locally so this module stays a leaf with no import-time
    dependency on the inquiry feature's service layer — the same reason
    Agent/BrokerRequirementAgent/requirement_normalization.py imports the
    matcher's normalization lazily."""
    trimmed = clean_text(value)
    if trimmed is None:
        return None
    from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

    normalized = normalize_phone(trimmed)
    if normalized is None:
        raise ValueError("That doesn't look like a valid phone number.")
    return normalized


def check_url(value: Optional[str]) -> Optional[str]:
    """A pasted link — must at least be http(s) with a host, so "not a url"
    cannot be stored as one."""
    trimmed = clean_text(value)
    if trimmed is None:
        return None
    if not _URL_RE.match(trimmed):
        raise ValueError("That link doesn't look like a web address — it should start with https://")
    return trimmed


def check_instagram_reel_url(value: Optional[str]) -> Optional[str]:
    """An Instagram reel/post link this application can actually read a
    shortcode out of. Anything else is refused rather than stored, because a
    stored non-link still counted as "this property has a reel" — which is
    what put a property with `reel = hello` into the landing page's Ready to
    Add list."""
    trimmed = clean_text(value)
    if trimmed is None:
        return None
    if not _URL_RE.match(trimmed) or not _REEL_RE.search(trimmed):
        raise ValueError(
            "That isn't an Instagram reel link — paste the full link, e.g. "
            "https://www.instagram.com/reel/XXXXXXXX/"
        )
    return trimmed


def is_instagram_reel_url(value: Optional[str]) -> bool:
    """The same question as check_instagram_reel_url, asked of a value that
    is ALREADY stored (so it must answer, not raise). What lets a link saved
    before this module existed stop counting as a reel."""
    if not value:
        return False
    return bool(_URL_RE.match(value.strip())) and bool(_REEL_RE.search(value))


def clean_name_list(values: Optional[List[str]]) -> List[str]:
    """A short list of free-text names (an agent's coverage areas, a
    requirement's preferred areas): trimmed, blanks dropped, de-duplicated
    case-insensitively keeping the first spelling typed, and capped. The
    same rule the Agents dialog already applies in the browser — applied
    here too, so the API cannot be handed
    ["Pal", "pal", " Vesu ", ""] and store it as-is."""
    if not values:
        return []
    cleaned: List[str] = []
    seen = set()
    for value in values:
        name = " ".join(str(value).split())
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name[:80])
        if len(cleaned) >= 40:
            break
    return cleaned


def canonical_property_type(value: Optional[str]) -> Optional[str]:
    """"flat" -> "Flat", "row-house / plot" -> "Row House, Plot". The same
    pure function the requirement side already runs on a hand-typed type
    (requirement_pipeline_service.create_requirement), reused here so a
    property typed in by hand filters and matches identically instead of
    sitting outside the Type filter's vocabulary.

    Only ever applied on the manual Add/Edit path — the LLM structuring
    stage is untouched."""
    from Agent.BrokerRequirementAgent import requirement_normalization

    return requirement_normalization.canonical_requirement_type(value)


def canonical_bhk(value: Optional[str]) -> Optional[str]:
    """"3" -> "3 BHK", "4bhk , 5bhk" -> "4 BHK, 5 BHK". Same reasoning as
    canonical_property_type above."""
    from Agent.BrokerRequirementAgent import requirement_normalization

    return requirement_normalization.canonical_bhk(value)


class ListingContentValidators(BaseModel):
    """The checks shared by the two request bodies that carry a LISTING's
    content — the Properties page's PropertyContentFields and the Builder
    Projects page's BuilderProjectContentFields. They are the same dialog on
    screen (see BuilderProjectContentFields' own docstring), so the rules
    live here once rather than being typed out twice and drifting apart.

    A mixin of validators only: each model still declares its own fields,
    including the ge/le bounds, which pydantic cannot inherit. check_fields
    is off so this stays usable by a model that one day carries a subset.

    Nothing here rewrites a listing captured from WhatsApp — these run on
    the request body, not on StructuredProperty (see this module's own
    docstring)."""

    @field_validator("contact_phone", check_fields=False)
    @classmethod
    def _v_contact_phone(cls, value: Optional[str]) -> Optional[str]:
        return check_contact_phone(value)

    @field_validator("location_url", check_fields=False)
    @classmethod
    def _v_location_url(cls, value: Optional[str]) -> Optional[str]:
        return check_url(value)

    @field_validator("instagram_reel_url", check_fields=False)
    @classmethod
    def _v_instagram_reel_url(cls, value: Optional[str]) -> Optional[str]:
        return check_instagram_reel_url(value)

    @field_validator("property_type", check_fields=False)
    @classmethod
    def _v_property_type(cls, value: Optional[str]) -> Optional[str]:
        return canonical_property_type(value)

    @field_validator("bhk", check_fields=False)
    @classmethod
    def _v_bhk(cls, value: Optional[str]) -> Optional[str]:
        return canonical_bhk(value)
