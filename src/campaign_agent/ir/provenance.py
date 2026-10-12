"""Where each value in the campaign came from.

confirmed  the user set or corrected it (answer, direct edit, or an edit request with a quote).
inferred   the model read it from the user's text; the quote is stored and was checked.
defaulted  filled from the contract default because nothing was said.
suggested  a web-research proposal. Never a field value: pending ones live in `ir.suggestions`
           until the user accepts one (then the field becomes `confirmed` with the source URLs).
unset      nothing is known. Fields without a provenance entry are unset.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class Status(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    DEFAULTED = "defaulted"
    SUGGESTED = "suggested"
    UNSET = "unset"


Url = Annotated[str, StringConstraints(pattern=r"^https?://[^\s]+$", max_length=500)]


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Status
    source_turn: int | None = None
    evidence_quote: Annotated[str, StringConstraints(max_length=300)] | None = None
    sources: tuple[Url, ...] = ()
