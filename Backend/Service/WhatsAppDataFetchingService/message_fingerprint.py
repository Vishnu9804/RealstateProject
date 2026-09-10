"""Content fingerprint for a raw WhatsApp message — how the pipeline
recognises a message it has already turned into properties, without ever
comparing message texts to each other.

Why a fingerprint instead of string comparison: broker groups re-post the
same listing text verbatim, over and over, and every re-post used to cost a
full LLM structuring call plus a duplicate-detection pass before anything
noticed. Catching that needs an "have I seen this exact text before?" check,
and doing that by pulling stored texts back out of the database and
comparing them one by one gets steadily more expensive as the table grows —
every check would scan every stored message, and message texts are long.

A hash collapses any text, however long, to a fixed 64-character digest.
Identical texts always produce the identical digest, so the question becomes
a single indexed equality lookup (see property_repository.
find_message_id_by_fingerprint) — the same cost as looking a user up by
email, whether there are a hundred stored messages or a million, with no
message text crossing the wire at all.

normalize() is what makes it tolerant of the cosmetic differences that
carry no meaning: case, and any run of whitespace (WhatsApp text is full of
stray double spaces, trailing spaces and non-breaking spaces that differ
between two otherwise character-identical forwards). Anything beyond that —
a genuinely reworded re-post, one changed digit — is deliberately NOT this
function's job: it hashes to something completely different, goes through
the normal LLM pipeline, and is judged there.
"""

from __future__ import annotations

import hashlib


def normalize(text: str) -> str:
    """Lowercased, with every run of whitespace collapsed to a single space
    and the ends trimmed. str.split() splits on Unicode whitespace, so
    non-breaking spaces and stray newlines collapse the same way ordinary
    spaces do."""
    return " ".join(text.split()).lower()


def fingerprint(text: str) -> str:
    """The 64-character hex digest of normalize(text). Callers must treat an
    empty normalization as "no fingerprint" (see is_fingerprintable) rather
    than hashing it — every blank message would otherwise share one
    fingerprint and collapse into a single entry."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def is_fingerprintable(text: str) -> bool:
    return bool(normalize(text))
