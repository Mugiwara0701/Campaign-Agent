"""The campaign intermediate representation: one typed source of truth, persisted per campaign.

Value types reuse the contract's aliases, so a value that fits the IR also fits the contract's
bounds. Cross-field rules (min_draft >= min_qualify ...) are NOT checked here: a conflict is a
question to the user (gap engine, M5) or a validation error (M3), not a rejected patch.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from campaign_agent.contract.model import (
    Confidence,
    EngineRestMinutes,
    FeedItems,
    MaxAgeDays,
    MaxNewLeads,
    MaxPagesPerDomain,
    MaxPagesPerRun,
    PagesPerQuery,
    QueriesPerRun,
    QuerySuffix,
    RelevanceTerms,
    RepeatAfterDays,
    ResultsPerQuery,
    Score,
    TermList,
    Tld,
)
from campaign_agent.ir.patch import UnsupportedKind
from campaign_agent.ir.provenance import Provenance, Url

# Controlled vocabulary for contacts.preferred_roles (changes doc B5). Free text is never accepted.
Role = Literal[
    "cto", "vp_engineering", "engineering_director", "engineering_manager", "founder", "procurement"
]
EmployeeCount = Annotated[int, Field(ge=1, le=1_000_000)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Meta(_M):
    id: str  # the dashboard's public.campaign.id
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    description: Annotated[str, StringConstraints(max_length=5000)] = ""
    contract_version: Literal["1.0"] = "1.0"
    revision: Annotated[int, Field(ge=0)] = 0


class Objective(_M):
    type: Literal["customer_discovery"] = "customer_discovery"  # the only objective the MA supports
    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class Audience(_M):
    industries: TermList = []
    products: TermList = []
    technologies: TermList = []
    capabilities_needed: TermList = []
    signals: TermList = []
    # Proposed contract extensions: stored, reported as "not applied" until the MA supports them.
    include_countries: TermList = []
    employee_min: EmployeeCount | None = None
    employee_max: EmployeeCount | None = None


class Qualification(_M):
    min_qualify: Score | None = None
    min_draft: Score | None = None
    min_confidence: Confidence | None = None
    accept_hiring_signals: bool | None = None
    accept_service_companies: bool | None = None
    max_age_days: MaxAgeDays | None = None
    min_relevance_terms: RelevanceTerms | None = None


class Contacts(_M):
    preferred_roles: Annotated[list[Role], Field(max_length=6)] = []  # proposed extension


class Exclusions(_M):
    companies: TermList = []
    terms: TermList = []
    countries: TermList = []
    tlds: Annotated[list[Tld], Field(max_length=30)] = []


class Limits(_M):
    queries_per_run: QueriesPerRun | None = None
    repeat_after_days: RepeatAfterDays | None = None
    results_per_query: ResultsPerQuery | None = None
    pages_per_query: PagesPerQuery | None = None
    use_job_feeds: bool | None = None
    feed_items: FeedItems | None = None
    query_suffix: QuerySuffix | None = None
    max_new_leads_per_run: MaxNewLeads | None = None
    max_pages_per_run: MaxPagesPerRun | None = None
    max_pages_per_domain: MaxPagesPerDomain | None = None
    engine_rest_minutes: EngineRestMinutes | None = None


class Approval(_M):
    """Read-only. Email approval is enforced by the Marketing Agent for every email."""

    required: Literal[True] = True


class StoredSuggestion(_M):
    id: str
    path: str
    value: JsonValue
    sources: tuple[Url, ...]
    rationale: str = ""
    status: Literal["suggested", "accepted", "rejected"] = "suggested"
    source_turn: int | None = None


class UnsupportedItem(_M):
    request: str
    kind: UnsupportedKind
    reason: str
    proposal: str | None = None
    source_turn: int | None = None


class CampaignIR(_M):
    meta: Meta
    objective: Objective = Objective()
    audience: Audience = Audience()
    qualification: Qualification = Qualification()
    contacts: Contacts = Contacts()
    exclusions: Exclusions = Exclusions()
    limits: Limits = Limits()
    approval: Approval = Approval()
    # Facts about our own organisation ("we are a 200-person BSP firm"). Kept, never emitted.
    organisation_context: Annotated[
        list[Annotated[str, StringConstraints(max_length=200)]], Field(max_length=10)
    ] = []
    provenance: dict[str, Provenance] = {}
    suggestions: Annotated[list[StoredSuggestion], Field(max_length=20)] = []
    unsupported: Annotated[list[UnsupportedItem], Field(max_length=30)] = []


def new_ir(campaign_id: str, name: str = "", description: str = "") -> CampaignIR:
    return CampaignIR(meta=Meta(id=campaign_id, name=name, description=description))
