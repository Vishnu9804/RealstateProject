"""The one place that knows how an Indian short-scale money word maps to a
multiplier — shared by both structuring stages (property_structurer.py's
prices and requirement_structurer.py's budgets) so a shorthand that works
in one can never be silently unknown to the other.

Why this exists as its own module: the vocabulary used to be a literal dict
duplicated in both files, listing only the textbook spellings (l/lakh/cr/k).
Real broker messages do not use the textbook spellings. A listing reading
"Rate - 95 Lk" cost a real property its ENTIRE price — not because the LLM
misread it (it extracted 95L / 9,500,000 correctly) but because the
deterministic cross-check that re-reads the raw message text
(property_structurer._verify_total_price_against_text) did not recognise
"Lk" as a scale word, found no total stated anywhere in the message, and
concluded the LLM had invented the price. It then cleared it, and the
Properties page showed a blank Price. One missing spelling, one silently
blanked field.

So the list below is deliberately generous about spelling. Every entry is
an unambiguous money word: adding a spelling can only ever let a figure the
broker really did write be recognised as the amount they meant, and none of
these words means anything else in a property message.

SCALE_WORD_PATTERN is ordered LONGEST-FIRST on purpose. Python's regex
alternation takes the first branch that matches, not the longest, so a
pattern anchored with \b behind "l|lakh" would try "l", fail the boundary
check against "akh", and give up on a perfectly good "45 lakh". Sorting by
length removes that whole class of bug from every pattern built on this.
"""

from __future__ import annotations

import re

CRORE = 10_000_000
LAKH = 100_000
THOUSAND = 1_000

SCALE_MULTIPLIERS = {
    # crore
    "cr": CRORE,
    "crs": CRORE,
    "cror": CRORE,
    "crore": CRORE,
    "crores": CRORE,
    "karod": CRORE,
    # lakh — by far the most abbreviated of the three in these groups
    "l": LAKH,
    "lk": LAKH,
    "lks": LAKH,
    "lkh": LAKH,
    "lac": LAKH,
    "lacs": LAKH,
    "lack": LAKH,
    "lacks": LAKH,
    "lakh": LAKH,
    "lakhs": LAKH,
    # thousand
    "k": THOUSAND,
    "thousand": THOUSAND,
    "thousands": THOUSAND,
    "hazar": THOUSAND,
    "hajar": THOUSAND,
}

# See the module docstring: longest-first, so `\b`-anchored patterns built
# from this never match a short prefix of a longer word.
SCALE_WORD_PATTERN = "|".join(
    re.escape(word) for word in sorted(SCALE_MULTIPLIERS, key=len, reverse=True)
)
