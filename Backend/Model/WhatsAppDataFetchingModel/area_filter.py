from typing import List

from pydantic import BaseModel


class AreaFilterSettings(BaseModel):
    """The list of area keywords a message's text must mention (case-
    insensitively, as a whole word/phrase) to be treated as a qualified
    property message."""

    keywords: List[str] = []
