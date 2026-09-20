"""The one place that turns whatever a phone number was written as into
this application's single stored shape: "+91" followed by exactly 10
digits, held as a LIST because one listing routinely has more than one
number on it.

WHY THIS EXISTS

The properties imported from the client's spreadsheet carry contact numbers
that were never one number in one box: two (and occasionally three) numbers
typed straight into the same cell with nothing between them, so
"9824750171" and "9825907179" arrived as the single 20-digit run
"98247501719825907179" — which is not a number anyone can call, and which no
amount of display formatting can repair. Nothing about that is the
spreadsheet's fault alone: a WhatsApp listing says "98765 43210 / 98765
43211" just as often. Both have to end up as two separate, dialable numbers.

WHAT IT GUARANTEES

- Every value it returns is either the canonical "+91XXXXXXXXXX" (13
  characters, exactly 10 digits after the country code) or — only when the
  text held no recognisable number at all — the original text, trimmed and
  length-capped, so a note somebody deliberately stored in a contact box
  ("ask at the site office") is never silently thrown away.
- Order is the order it was written in; the first entry is the primary
  number every one-line display shows.
- Duplicates are dropped (the same number written twice, once with +91 and
  once without, is one number).

WHAT IT DELIBERATELY DOES NOT DO

It does not use the `phonenumbers` library. That library answers "is this
one string one valid number", which is the question
Service/WhatsAppInquiryHandlingService/phone_utils.py asks for a client's
primary key and an agent's WhatsApp number — a question where a wrong
answer creates a duplicate person, so a strict parse is the right tool.
This module answers a different question: "how many numbers are hiding in
this text, and where does each one start and end". phonenumbers cannot
split a 20-digit run, and calling it per candidate here would cost a full
metadata parse per property on every import pass for no extra certainty.

NUMBERING RULES ENCODED HERE (India only — see the module's one region
assumption below)

- A mobile number is 10 digits starting 6, 7, 8 or 9.
- A landline written in full national form is also 10 digits (STD code
  without its trunk "0", plus the local number), starting 2-9.
- The same number may arrive as 10 digits, 0 + 10, 91 + 10, 091 + 10 or
  0091 + 10, with any mix of spaces, dashes, dots and brackets inside it.

This application handles Indian real estate only, and every number in it —
broker, owner, client, agent — is an Indian number. That is why "+91" is a
constant here rather than a parsed country code: a 10-digit run with no
country code is unambiguous under that assumption, and it is the assumption
that lets a merged run be split at all.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

# The country code every stored number carries. See the module docstring on
# why this is a constant.
COUNTRY_CODE = "+91"

# The canonical stored form's exact length: "+91" plus 10 digits.
CANONICAL_LENGTH = 13

# The number of digits in an Indian number once the country code and any
# trunk prefix are off it.
_NATIONAL_DIGITS = 10

# A mobile number's leading digit. Used when deciding where to CUT a run of
# digits that holds more than one number — deliberately narrower than
# _NATIONAL_START below, because a cut is a guess and a wrong guess invents
# two numbers out of one. Landlines are accepted whole (see _peel) but never
# used as a cut point.
_MOBILE_START = frozenset("6789")

# A full national number's leading digit: mobiles (6-9) plus landline STD
# codes (2-5). 0 and 1 are never the first digit of a national number.
_NATIONAL_START = frozenset("23456789")

# Characters that DO separate one number from the next. A slash, a comma, a
# semicolon, a pipe, an ampersand or a line break between two numbers means
# two numbers — never one long one.
_SEPARATORS = ",/;|&\n\r\t+"

# Word separators, normalised to a comma before splitting so "9876543210 and
# 9876543211" and "9876543210 or 9876543211" split the same way a comma
# would. Surrounded by spaces on both sides so a society called "Sandeep" or
# a name containing "or" is never cut in half.
_WORD_SEPARATORS = (" and ", " or ")

# How many numbers one contact field may hold. Far above the two or three a
# real listing carries, and low enough that a pasted block of text cannot
# turn into a hundred-entry list.
MAX_NUMBERS = 10

# The cap on an entry that could NOT be read as a number and is therefore
# kept as written (see the module docstring). Matches the free-text cap the
# client-records side already applies to its own unverified phone list
# (Service/WhatsAppInquiryHandlingService/manual_client_service.py).
MAX_RAW_LENGTH = 40


def is_canonical(value: Any) -> bool:
    """Whether `value` is ALREADY in the stored shape — the cheap check that
    lets every read path skip normalisation entirely. Deliberately a few
    string comparisons and no regex: this runs once per number on every
    property loaded into the in-memory snapshot."""
    return (
        isinstance(value, str)
        and len(value) == CANONICAL_LENGTH
        and value.startswith(COUNTRY_CODE)
        and value[3:].isdigit()
    )


def _digits_only(chunk: str) -> str:
    return "".join(character for character in chunk if character.isdigit())


def _peel(digits: str) -> Optional[tuple]:
    """Reads ONE national number off the front of `digits` and returns
    (ten_digits, rest), or None when the front of the run cannot be a
    number.

    The order of the tests is the whole of the logic. An exact-length match
    on the WHOLE run is tried first, because a run that is exactly one
    number is the overwhelmingly common case and must never be cut: the
    12-digit "919824750171" is one number with a country code, not the
    10-digit "9198247501" plus a stray "71". Only once the run is longer
    than any single spelling of one number is it treated as several numbers
    written together, and then the cut is made after a leading country/trunk
    prefix if there is one, and otherwise after 10 digits — and only ever
    where the number being cut off starts like a mobile, since that is the
    only case a merged run has ever actually been seen in."""
    length = len(digits)

    # --- the whole run is exactly one number, in one of its five spellings
    if length == _NATIONAL_DIGITS and digits[0] in _NATIONAL_START:
        return digits, ""
    if length == 11 and digits[0] == "0" and digits[1] in _NATIONAL_START:
        return digits[1:], ""
    if length == 12 and digits[:2] == "91" and digits[2] in _NATIONAL_START:
        return digits[2:], ""
    if length == 13 and digits[:3] == "091" and digits[3] in _NATIONAL_START:
        return digits[3:], ""
    if length == 14 and digits[:4] == "0091" and digits[4] in _NATIONAL_START:
        return digits[4:], ""

    # --- longer than any one number: cut one mobile off the front
    if length > 12 and digits[:2] == "91" and digits[2] in _MOBILE_START:
        return digits[2:12], digits[12:]
    if length > 11 and digits[0] == "0" and digits[1] in _MOBILE_START:
        return digits[1:11], digits[11:]
    if length > _NATIONAL_DIGITS and digits[0] in _MOBILE_START:
        return digits[:_NATIONAL_DIGITS], digits[_NATIONAL_DIGITS:]

    return None


def _numbers_in_run(digits: str) -> List[str]:
    """Every national number in one unbroken run of digits, or [] when the
    run cannot be read cleanly end to end.

    All-or-nothing on purpose: a run that peels into two numbers and then
    leaves four digits over was never two numbers and a fragment — it was
    something this module does not understand, and the honest answer is to
    hand the whole thing back untouched (the caller keeps it as written)
    rather than to store two numbers invented out of a misreading."""
    numbers: List[str] = []
    rest = digits
    while rest:
        peeled = _peel(rest)
        if peeled is None:
            return []
        national, rest = peeled
        numbers.append(national)
        if len(numbers) > MAX_NUMBERS:
            return []
    return numbers


def _chunks(raw: str) -> List[str]:
    """The raw text cut at every separator that means "the next number
    starts here", with single-number formatting left alone."""
    lowered = raw.lower()
    for word in _WORD_SEPARATORS:
        lowered = lowered.replace(word, ",")
    translated = "".join("," if character in _SEPARATORS else character for character in lowered)
    return [chunk for chunk in translated.split(",") if chunk.strip()]


def split_phone_numbers(raw: Any) -> List[str]:
    """Every number in one free-text contact value, canonicalised.

    This is the function that repairs the imported spreadsheet's merged
    cells and that reads a WhatsApp listing's "call 98765 43210 / 98765
    43211" as the two numbers it is. Never raises — see check_phone_numbers
    in Model/field_validation.py for the form-facing rule that does."""
    if raw is None:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    trimmed = raw.strip()
    if not trimmed:
        return []

    found: List[str] = []
    for chunk in _chunks(trimmed):
        for national in _numbers_in_run(_digits_only(chunk)):
            found.append(COUNTRY_CODE + national)

    if not found:
        # Nothing in it parsed as a number. Keep what was written rather
        # than dropping it — see the module docstring.
        return [trimmed[:MAX_RAW_LENGTH]]

    return _deduplicate(found)


def _deduplicate(values: Iterable[str]) -> List[str]:
    """First spelling of each number wins, order kept, count capped."""
    unique: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
        if len(unique) >= MAX_NUMBERS:
            break
    return unique


def normalize_phone_list(values: Any) -> List[str]:
    """A whole stored/submitted list brought into the canonical shape.

    The fast path — every entry already canonical, which is the case for
    every value read back out of the database after the one-time migration —
    skips the splitting entirely and only de-duplicates, so a page load that
    builds thousands of properties does a handful of character comparisons
    per number and nothing else. De-duplicating even there is not optional:
    a form that offers an Add button will sooner or later be handed the same
    number twice, and the second copy has to go whether or not it arrived
    already canonical."""
    if values is None:
        return []
    if isinstance(values, str):
        return split_phone_numbers(values)
    if not isinstance(values, (list, tuple)):
        return []

    if all(is_canonical(value) for value in values):
        return _deduplicate(values)

    expanded: List[str] = []
    for value in values:
        expanded.extend(split_phone_numbers(value))
    return _deduplicate(expanded)


def primary_phone(values: Any) -> Optional[str]:
    """The one number every one-line display and every existing
    `contact_phone` reader sees. None when there is none."""
    if not values:
        return None
    if isinstance(values, str):
        return values or None
    for value in values:
        if value:
            return value
    return None


def format_for_display(value: Optional[str]) -> Optional[str]:
    """"+919824750171" -> "+91 9824750171". Anything that is not canonical
    (a legacy note kept as written) comes back untouched — this is display
    only and must never hide what is stored."""
    if not value:
        return None
    if not is_canonical(value):
        return value
    return f"{COUNTRY_CODE} {value[3:]}"
