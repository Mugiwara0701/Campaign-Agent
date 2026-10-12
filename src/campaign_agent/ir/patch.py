"""What the model (and the dashboard) may send: closed schemas, nothing else is accepted.

The model never writes YAML or contract keys. It emits operations on IR paths; the merger
decides what is applied. An unknown path is not an error here: it becomes an "unsupported" item.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from campaign_agent.ir.provenance import Url


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Path = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Quote = Annotated[str, StringConstraints(max_length=300)]

UnsupportedKind = Literal["channel", "approval", "outreach_content", "other"]
ResearchKind = Literal[
    "term_meaning", "product_categories_for_technology", "industries_for_capability"
]


class PatchOp(_Closed):
    path: Path
    op: Literal["set", "add", "remove", "clear"] = "set"
    value: JsonValue = None  # null means "missing", except for a direct edit by the user
    evidence_quote: Quote | None = None  # required for model-made ops, checked against the text


class UnsupportedClaim(_Closed):
    """Something the user asked for that has no field at all (LinkedIn, tone, approval off)."""

    request: Annotated[str, StringConstraints(max_length=300)]
    kind: UnsupportedKind = "other"


class ResearchNeed(_Closed):
    kind: ResearchKind
    term: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class IRPatch(_Closed):
    ops: Annotated[list[PatchOp], Field(max_length=40)] = []
    unsupported: Annotated[list[UnsupportedClaim], Field(max_length=10)] = []
    research_needs: Annotated[list[ResearchNeed], Field(max_length=3)] = []


class Suggestion(_Closed):
    """A web-research proposal. Needs at least one source URL; never applied by itself."""

    path: Path
    value: JsonValue
    sources: Annotated[list[Url], Field(min_length=1, max_length=5)]
    rationale: Annotated[str, StringConstraints(max_length=300)] = ""
