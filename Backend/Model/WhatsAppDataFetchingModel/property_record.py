from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty


class PropertyRecord(StructuredProperty):
    """A StructuredProperty as returned by the API — adds the
    already-formatted IST timestamp (DD/MM/YYYY, 12h or 24h per the current
    display setting) so the frontend never has to do timezone/format math
    itself."""

    formatted_timestamp: str
    # How many photos this property has. Always accurate, but on the list
    # endpoint (GET /properties) it's computed in SQL WITHOUT loading the
    # actual image_urls column — see Database/property_repository.py's
    # get_all_properties_summary for why (that column can hold several
    # megabytes of base64 photo data per row, and the list view only ever
    # needs a count badge, never the pixels). image_urls itself is [] on
    # that endpoint; the frontend fetches GET /properties/{record_id} for
    # the real photos when it actually needs to display them (the detail
    # dialog, the Edit dialog).
    image_count: int = 0
