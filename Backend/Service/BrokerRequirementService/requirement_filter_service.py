"""The cheap string-search stage that decides whether an incoming message is
a REQUIREMENT (someone asking FOR a property) rather than a property
listing. It runs on every message the data-fetching intake claims, right
alongside area_filter_service's property-relevance filter, and BEFORE any
LLM call — see Service/WhatsAppDataFetchingService/whatsapp_service.py for
where the two meet.

The rule it enforces is a hard split, by product decision: a message that
looks like a requirement is NEVER treated as a property. It is not
structured into a property model, it is never shown to the property LLM
prompt, it is not area-matched and it is not duplicate-checked. It either
goes to the requirement pipeline (when the chat it came from is selected
under Requirement on the Connection page) or it goes nowhere at all.

Because that split can COST a real listing when it fires wrongly, this
filter is built the opposite way round from area_filter_service. That one is
deliberately loose, because a false positive there is cheap — the LLM stage
simply rejects a stray greeting. A false positive HERE silently drops a
property, so precision, not recall, is the goal.

Precision comes from splitting the vocabulary by grammatical role and
guarding each role against the way listings actually use those same words:

  NOUN form ("requirement", "zaroorat", "jarurat")
      "Client requirement: 3BHK Vesu" is a genuine demand, so a preceding
      person-noun must NOT block it. Only paperwork/money nouns do
      ("no requirement of brokerage").

  VERB / participle form ("required", "req", "needed", "chahiye",
  "joie chhe", "wanted")
      These take their object BEFORE them, which is exactly how a listing
      asks for the other side of its own deal: "buyer required", "customer
      chahiye", "tenant wanted". A preceding person-noun blocks them, as do
      paperwork/money nouns ("brokerage required", "documents needed") and
      a following "to"/"be" ("need to sell urgently" is the sender's own
      intent, not a demand for a property).

  PHRASE form ("looking for", "in search of")
      These take their object AFTER them, so the guard looks the other way:
      "looking for genuine buyer" is a listing, "looking for 3BHK in Vesu"
      is a requirement. Only the next two words are examined, so a later
      "...for my client" can never block a real requirement.

Everything is precompiled at import time, so the per-message cost is a fixed
number of single-pass regex searches no matter how long the word lists get.
This filter has no settings and no database: unlike the area keywords, the
vocabulary of "I am looking for a property" does not change per client.
"""

from __future__ import annotations

import re
from typing import List, Optional, Pattern

# --- vocabulary ------------------------------------------------------------

# NOUN form — "a requirement" as a thing someone has.
_NOUN_SIGNALS: List[str] = [
    r"requirements?",
    r"requirment?s?",  # very common misspelling in these groups
    # Hindi/Gujarati "zaroorat"/"jarurat" (need) across its many spellings.
    r"[zj]a+r+o*u*r+a*t",
    r"[zj]aruriyat",
    r"talash",
    # Native script, for messages that are not transliterated at all.
    r"ज़रूरत",
    r"जरूरत",
    r"आवश्यकता",
    r"જરૂર",
]

# VERB / participle form — object comes BEFORE the word.
_VERB_SIGNALS: List[str] = [
    r"required",
    r"requires",
    r"require",
    r"req\.?",
    r"needed",
    r"needs",
    r"need",
    r"wanted",
    # Hindi "chahiye" and its spellings.
    r"chahiye",
    r"chahiyee",
    r"chaahiye",
    r"chahiya",
    r"chahie",
    r"chaiye",
    r"chahye",
    # Gujarati "joie chhe" / "joiye chhe" and its spellings.
    r"joiye",
    r"joie",
    r"joye",
    r"joita",
    r"joiti",
    r"joito",
    r"mange",
    # Native script.
    r"चाहिए",
    r"જોઈએ",
    r"જોઇએ",
]

# PHRASE form — object comes AFTER the phrase.
_PHRASE_SIGNALS: List[str] = [
    r"looking\s+for",
    r"in\s+search\s+of",
    r"any\s?one\s+has",
    r"any\s?one\s+having",
    r"enquiry\s+for",
]

# Paperwork, money and process nouns. A signal directly after one of these
# is a condition attached to an OFFER ("brokerage required", "2 months
# deposit needed"), never a demand for a property. "no" is here for
# "no requirement of ...".
_PAPERWORK_NOUNS: List[str] = [
    "no", "brokerage", "brokrage", "commission", "deposit", "token", "advance", "loan", "finance",
    "emi", "document", "documents", "docs", "paper", "papers", "noc", "agreement", "aadhar",
    "aadhaar", "pan", "kyc", "id", "photo", "photos", "pic", "pics", "registration", "gst",
    "cheque", "cash", "passport", "licence", "license", "permission", "approval", "signature",
    "sign", "stamp", "maintenance", "bank", "verification", "detail", "details", "info",
    "information", "reference", "proof",
    # The units and paper-words those conditions are counted in: "advance 3
    # months required", "token amount required", "PAN card required".
    "amount", "card", "cards", "copy", "copies", "month", "months", "day", "days", "year",
    "years", "letter", "letters", "slip", "receipt", "bill", "bills", "statement", "form",
    "forms", "certificate", "affidavit", "security", "notice", "percent", "%",
]

# People, not properties. A listing asks for the OTHER side of its own deal
# — these are what it asks for. Blocks the verb form before it
# ("buyer required") and the phrase form after it ("looking for a buyer"),
# but deliberately NOT the noun form ("client requirement" is a real demand).
_PERSON_NOUNS: List[str] = [
    "buyer", "buyers", "byer", "customer", "customers", "client", "clients", "tenant", "tenants",
    "party", "parties", "purchaser", "purchasers", "investor", "investors", "broker", "brokers",
    "partner", "partners", "seller", "sellers", "owner", "owners", "dealer", "dealers", "agent",
    "agents", "guest", "guests", "member", "members", "staff", "employee", "employees", "person",
    "people", "candidate", "candidates", "grahak", "gharak", "ghirak",
]

# --- compiled patterns -----------------------------------------------------

_NOUN_PATTERN: Pattern[str] = re.compile(r"\b(?:" + "|".join(_NOUN_SIGNALS) + r")\b", re.IGNORECASE)
_VERB_PATTERN: Pattern[str] = re.compile(r"\b(?:" + "|".join(_VERB_SIGNALS) + r")\b", re.IGNORECASE)
_PHRASE_PATTERN: Pattern[str] = re.compile(r"\b(?:" + "|".join(_PHRASE_SIGNALS) + r")\b", re.IGNORECASE)


def _preceding_word_pattern(words: List[str]) -> Pattern[str]:
    """Matches one of `words` sitting immediately before a hit — anchored to
    the END of the lookbehind slice, with at most a few separator characters
    (space, colon, dash) in between, so only the word DIRECTLY before the
    hit is ever considered."""
    return re.compile(
        r"(?:" + "|".join(re.escape(word) for word in words) + r")[^A-Za-z0-9]{0,3}$", re.IGNORECASE
    )


_PAPERWORK_BEFORE_RE = _preceding_word_pattern(_PAPERWORK_NOUNS)
_PERSON_OR_PAPERWORK_BEFORE_RE = _preceding_word_pattern(_PAPERWORK_NOUNS + _PERSON_NOUNS)
# "need to sell", "require to be" — the token is a verb about the sender's
# own intent rather than a demand for a property.
_INFINITIVE_AFTER_RE: Pattern[str] = re.compile(r"^[^A-Za-z0-9]{0,3}(?:to|be|been|being)\b", re.IGNORECASE)
# Only the next TWO words after a phrase signal are examined — enough to
# catch "looking for genuine buyer", short enough that "looking for 3BHK for
# my client" is never mistaken for one.
_PERSON_IN_NEXT_TWO_WORDS_RE: Pattern[str] = re.compile(
    r"^(?:[^A-Za-z0-9]+[A-Za-z0-9]+){0,1}[^A-Za-z0-9]+(?:"
    + "|".join(re.escape(word) for word in _PERSON_NOUNS)
    + r")\b",
    re.IGNORECASE,
)

# How far back/forward a hit looks for its context. Long enough to clear the
# longest word in the lists plus a separator, short enough that a noun from
# an unrelated clause can never reach it.
_CONTEXT_CHARS = 24


def is_requirement_message(text: str) -> bool:
    """True if `text` looks like someone asking FOR a property.

    A True here means the message is routed to the requirement pipeline and
    is deliberately NOT considered as a property — see the module docstring
    for why that makes precision, not recall, the goal of this filter."""
    if not text:
        return False
    return _matched_signal(text) is not None


def matched_signal(text: str) -> Optional[str]:
    """The exact word/phrase that classified this message as a requirement,
    or None. Used only for log lines — `is_requirement_message` is the
    decision; this just explains it, so a wrong split is diagnosable from
    the terminal without re-deriving it by hand."""
    if not text:
        return None
    return _matched_signal(text)


def _matched_signal(text: str) -> Optional[str]:
    for match in _NOUN_PATTERN.finditer(text):
        if not _blocked_before(text, match.start(), _PAPERWORK_BEFORE_RE):
            return match.group(0)

    for match in _VERB_PATTERN.finditer(text):
        if _blocked_before(text, match.start(), _PERSON_OR_PAPERWORK_BEFORE_RE):
            continue
        if _INFINITIVE_AFTER_RE.match(text[match.end() : match.end() + _CONTEXT_CHARS]):
            continue
        return match.group(0)

    for match in _PHRASE_PATTERN.finditer(text):
        if _PERSON_IN_NEXT_TWO_WORDS_RE.match(text[match.end() : match.end() + _CONTEXT_CHARS * 2]):
            continue
        return match.group(0)

    return None


def _blocked_before(text: str, start: int, pattern: Pattern[str]) -> bool:
    return pattern.search(text[max(0, start - _CONTEXT_CHARS) : start]) is not None
