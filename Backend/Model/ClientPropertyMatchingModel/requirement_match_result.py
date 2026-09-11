from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty


class RequirementMatchResult(BaseModel):
    """What the Broker Requirements page's "Match properties" dialog reads —
    the demand side's mirror of ClientMatchResult: one broker requirement's
    matched properties, already grouped into the same three buckets (see
    Model/ClientPropertyMatchingModel/match_bucket.py) and sorted
    highest-score-first within each.

    Deliberately the SAME MatchedProperty shape a client match uses. The
    scoring is literally the same code (Service/ClientPropertyMatchingService/
    scoring.py, reached through requirement_matching_service's adapter), so
    the result has no business looking different — and the frontend cards,
    badges and bucket labels are shared as a direct consequence.

    Two things it does NOT share with ClientMatchResult, both because
    requirement matches are computed on demand rather than cached (see
    requirement_matching_service's own docstring):

      - `computed_at` is always "just now", never "whenever the nightly run
        last looked at this";
      - there is no counts-only variant, because nothing polls this for a
        badge — it is only ever read when a human opens the dialog.
    """

    record_id: str
    # What this requirement is asking for, in one line ("3 BHK Flat · to
    # buy · Vesu, Althan") — the dialog's own subtitle. Composed here rather
    # than in the frontend so the summary and the requirement fields
    # actually scored can never describe two different things.
    requirement_summary: str
    # False when the requirement carries none of the fields scoring can
    # compare (no type, no BHK, no budget, no area, no description) — the
    # dialog shows "nothing to match on" rather than an empty result that
    # looks like "no properties fit".
    has_requirements: bool
    computed_at: Optional[datetime] = None
    high: List[MatchedProperty] = []
    medium: List[MatchedProperty] = []
    low: List[MatchedProperty] = []
