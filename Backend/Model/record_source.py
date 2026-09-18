"""The canonical `source` values — WHERE a stored record came from.

One module rather than a constant per feature because the same vocabulary is
shared by the three tables that carry it (`clients`, `properties`,
`broker_requirements`): a value typed by hand in one place and compared
against a literal in another is exactly how a provenance field quietly stops
being trustworthy.

Stored as a plain VARCHAR / `str`, deliberately NOT a Postgres enum and NOT a
pydantic `Literal`: a new intake channel (a portal feed, a partner API) must
be a one-line addition here, never a migration, and a value written by an
older build must never fail validation on read. The constants below are what
every writer uses; anything else is a bug at the call site, not something the
model should reject at read time.

SOURCE_UNKNOWN is the only value nothing ever writes deliberately. It is what
rows written BEFORE this field existed were backfilled to (see
Database/session.py) wherever their real origin could not be established from
data already on the row — "we genuinely don't know", which is the honest
answer and is distinguishable from every real channel.
"""

from __future__ import annotations

# Typed in by a member of staff on the dashboard — the Inquiries page's
# Add Client dialog, the Properties page's Add dialog, the Broker
# Requirements page's Add dialog.
SOURCE_MANUAL = "manual"
# Extracted by the LLM pipeline from a message that arrived on a linked
# WhatsApp number, or (for a client) a requirements form opened from the
# link the WhatsApp welcome message sent.
SOURCE_WHATSAPP = "whatsapp"
# The requirements form opened from an Instagram DM link.
SOURCE_INSTAGRAM = "instagram"
# The public requirements form on the website — reached by anyone, with the
# phone number proved by OTP rather than by a token we issued.
SOURCE_WEBSITE_FORM = "website_form"
# A property enquiry submitted from the public landing site. Distinct from
# SOURCE_WEBSITE_FORM on purpose: this person never filled in requirements,
# they asked about one specific property, and what is stored for them is
# inferred from that property rather than stated by them.
SOURCE_WEBSITE_ENQUIRY = "website_enquiry"
# A bulk spreadsheet import.
SOURCE_EXCEL = "excel"
# Not written by any path — see the module docstring.
SOURCE_UNKNOWN = "unknown"

CLIENT_SOURCES = (
    SOURCE_MANUAL,
    SOURCE_WHATSAPP,
    SOURCE_INSTAGRAM,
    SOURCE_WEBSITE_FORM,
    SOURCE_WEBSITE_ENQUIRY,
    SOURCE_EXCEL,
    SOURCE_UNKNOWN,
)

PROPERTY_SOURCES = (SOURCE_MANUAL, SOURCE_WHATSAPP, SOURCE_EXCEL, SOURCE_UNKNOWN)

REQUIREMENT_SOURCES = (SOURCE_MANUAL, SOURCE_WHATSAPP, SOURCE_EXCEL, SOURCE_UNKNOWN)
