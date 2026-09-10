"""Tiny, shared field-coercion helpers for the GLM extraction schemas
(glm_extraction_schema.py's GLMPropertyListing and
glm_requirement_schema.py's GLMRequirementItem).

Observed in production: for a "bhk" field GLM sometimes emits a bare JSON
number (`"bhk": 2`) instead of the string the schema asks for and the
prompt's own examples show (`"bhk": "2 BHK"`) — most often when the message
just says a plain "2bhk"/"2 BHK" with nothing else distinguishing it. Before
this existed, that single field failing strict string validation raised a
pydantic ValidationError for the WHOLE response, discarding every property
or requirement in the entire batch over one cosmetic type slip — exactly the
kind of silent, disproportionate data loss both pipelines are built to
avoid everywhere else (see property_structurer.py's own repeated "never
lose a real record over a transcription slip" reasoning, e.g.
_resolve_message_id).

`coerce_bhk_field` is used as a `mode="before"` validator on `bhk` in both
schemas: it normalizes a bare number to the same shape the model was asked
for ("2 BHK"), while leaving an already-correct string (or None) untouched.
It is deliberately narrow — it does not touch any other field, and it never
invents a bedroom count that wasn't in the response, only reformats one that
was.
"""

from __future__ import annotations

from typing import Any, Optional


def coerce_bhk_field(value: Any) -> Optional[Any]:
    """Normalizes a raw `bhk` value straight out of GLM's JSON before
    pydantic's own str validation ever sees it. A bare int/float (2, 2.0,
    2.5) becomes "2 BHK"/"2.5 BHK" — the only sensible reading of a plain
    number in a field whose entire meaning IS the bedroom count. Anything
    else (a proper string already, None, or some other unexpected type) is
    passed through unchanged and left for pydantic's normal validation to
    accept or reject."""
    if isinstance(value, bool):
        # bool is a subclass of int in Python — explicitly excluded so a
        # stray `true`/`false` is never reformatted into a fake "1 BHK".
        return value
    if isinstance(value, int):
        return f"{value} BHK"
    if isinstance(value, float):
        # Whole numbers print as "2 BHK", not "2.0 BHK" — matching how a
        # broker would actually write it and how the LLM's own output
        # examples in the prompt are shaped.
        trimmed = int(value) if value.is_integer() else value
        return f"{trimmed} BHK"
    return value
