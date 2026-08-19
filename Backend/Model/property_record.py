from Model.structured_property import StructuredProperty


class PropertyRecord(StructuredProperty):
    """A StructuredProperty as returned by the API — adds the
    already-formatted IST timestamp (DD/MM/YYYY, 12h or 24h per the current
    display setting) so the frontend never has to do timezone/format math
    itself."""

    formatted_timestamp: str
