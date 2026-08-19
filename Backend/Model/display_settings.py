from pydantic import BaseModel


class DisplaySettings(BaseModel):
    """Whether timestamps are shown in 24-hour time (e.g. 14:05) or
    12-hour time with AM/PM (e.g. 2:05 PM). Dates are always DD/MM/YYYY."""

    use_24_hour_format: bool = False
