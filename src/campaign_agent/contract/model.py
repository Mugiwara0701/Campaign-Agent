"""Stand-in contract for the Marketing Agent's config/leadgen.yaml.

STAND-IN: this models the keys the MA reads today (traced in field_metadata.py). It is stricter
than the MA's own loader, which ignores unknown keys. When the MA ships its strict loader and
contract package, this module is replaced by that package. Until then "valid here" does not
prove the MA accepts the file (see M8, level L6).

Two models:
  LeadgenFile      the whole file as the MA reads it (base config, golden file).
  CampaignOverlay  only the campaign-owned keys the CA may emit. Everything else is rejected.

Each default and each bound is declared once: bounds in the aliases below, defaults on LeadgenFile.
The numeric maxima are PROPOSED values chosen around the base config, not MA limits.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CONFIG_VERSION = "1.0"

# Where base_config.yaml came from: a verbatim copy. To re-sync, copy the file and update these.
BASE_CONFIG_SOURCE = {
    "repo": "Marketing-AI-Agent",
    "file": "config/leadgen.yaml",
    "commit": "79a29d8d6ca754e3f0f78665aff78a458fcd7820",
    "sha256": "5fc679b686b353aec5ef42126832fda537021d01aec6f71e953fb651a1a6a9fa",
}

# --- shared types ---

# Short campaign text (products, industries, exclusions). No quotes, newlines or YAML punctuation.
Term = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^\w[\w .,'&/+()#-]*$",
    ),
]
TermList = Annotated[list[Term], Field(max_length=30)]  # campaign-sized lists
# base config lists (the exclusions are long)
BaseTermList = Annotated[list[Term], Field(max_length=300)]
# Free vocabulary text kept in the base config (phrases, labels). Not emitted by the CA.
Phrase = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Tld = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\.[a-z0-9]{2,12}(\.[a-z0-9]{2,12})?$")
]

QueriesPerRun = Annotated[int, Field(ge=1, le=40)]
RepeatAfterDays = Annotated[int, Field(ge=0, le=90)]
ResultsPerQuery = Annotated[int, Field(ge=1, le=30)]
PagesPerQuery = Annotated[int, Field(ge=1, le=10)]
FeedItems = Annotated[int, Field(ge=1, le=100)]
QuerySuffix = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=80, pattern=r"^[\w .,'&/+()#-]*$"),
]
Score = Annotated[int, Field(ge=0, le=100)]
Confidence = Annotated[float, Field(ge=0, le=1)]
MaxAgeDays = Annotated[int, Field(ge=1, le=730)]
RelevanceTerms = Annotated[int, Field(ge=0, le=10)]
MaxNewLeads = Annotated[int, Field(ge=1, le=50)]
MaxPagesPerRun = Annotated[int, Field(ge=1, le=300)]
MaxPagesPerDomain = Annotated[int, Field(ge=1, le=10)]
EngineRestMinutes = Annotated[float, Field(ge=1, le=240)]
ContactPages = Annotated[int, Field(ge=1, le=8)]
ContactAttempts = Annotated[int, Field(ge=1, le=5)]
HostDelay = Annotated[float, Field(ge=0, le=60)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- full file (as the MA reads it) ---


class ProjectContext(_Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    industries: TermList = []
    products: TermList = []
    technologies: TermList = []
    hardware: TermList = []  # alias the MA merges into technologies
    engineering_requirements: TermList = []


class Search(_Strict):
    queries_per_run: QueriesPerRun = 16
    repeat_after_days: RepeatAfterDays = 14
    results_per_query: ResultsPerQuery = 15
    use_job_feeds: bool = False
    feed_items: FeedItems = 40
    pages_per_query: PagesPerQuery = 6
    query_suffix: QuerySuffix = ""
    # Vocabulary: maintained in the base config, never written by the CA.
    capabilities: Annotated[list[Phrase], Field(min_length=1)]
    products: Annotated[list[Phrase], Field(min_length=1)]
    industries: Annotated[list[Phrase], Field(min_length=1)]
    platforms: Annotated[list[Phrase], Field(min_length=1)]  # may contain "" (no platform)
    builder_phrases: Annotated[list[Phrase], Field(min_length=1)]
    need_phrases: Annotated[list[Phrase], Field(min_length=1)]
    rfp_words: Annotated[list[Phrase], Field(min_length=1)]


class Qualification(_Strict):
    min_qualify: Score = 50
    min_draft: Score = 60
    min_confidence: Confidence = 0.5
    accept_hiring_signals: bool = True
    accept_service_companies: bool = False
    max_age_days: MaxAgeDays = 120
    min_relevance_terms: RelevanceTerms = 2


class Contacts(_Strict):
    max_pages: ContactPages = 4
    max_attempts: ContactAttempts = 3
    link_labels: Annotated[list[Annotated[list[Phrase], Field(min_length=1)]], Field(min_length=1)]


class Limits(_Strict):
    max_new_leads_per_run: MaxNewLeads = 10
    max_pages_per_run: MaxPagesPerRun = 100
    max_pages_per_domain: MaxPagesPerDomain = 6
    host_delay_seconds: HostDelay = (
        2.0  # DEAD: no MA code reads it. Accepted here only so the real file loads.
    )
    engine_rest_minutes: EngineRestMinutes = 30


class LeadgenFile(_Strict):
    config_version: Literal["1.0"] | None = None  # the MA file has none yet (changes doc A2)
    search: Search
    project_context: ProjectContext = ProjectContext()
    qualification: Qualification = Qualification()
    exclude_terms: BaseTermList = []
    exclude_companies: BaseTermList = []
    exclude_countries: BaseTermList = []
    exclude_tlds: Annotated[list[Tld], Field(max_length=100)] = []
    # Only companies in these countries (empty: any country not excluded). Set by a campaign, never by the base.
    include_countries: TermList = []
    contacts: Contacts
    limits: Limits = Limits()
    desktop_engines: Annotated[list[Phrase], Field(min_length=1)] = ["default"]

    @model_validator(mode="after")
    def _business_rules(self) -> "LeadgenFile":
        check_rules(
            min_qualify=self.qualification.min_qualify,
            min_draft=self.qualification.min_draft,
            queries_per_run=self.search.queries_per_run,
            pages_per_query=self.search.pages_per_query,
            results_per_query=self.search.results_per_query,
            max_pages_per_run=self.limits.max_pages_per_run,
        )
        return self


# --- campaign overlay (what the CA may emit) ---


SafeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=200, pattern=r"^[\w .,'&/+()#-]*$"),
]


class OverlayProjectContext(_Strict):
    """Same as ProjectContext without the `hardware` alias: the CA writes technologies."""

    name: SafeName = ""
    industries: TermList = []
    products: TermList = []
    technologies: TermList = []
    engineering_requirements: TermList = []


class OverlaySearch(_Strict):
    capabilities: Annotated[list[Phrase], Field(min_length=1, max_length=30)] | None = (
        None  # subset of base
    )
    queries_per_run: QueriesPerRun | None = None
    repeat_after_days: RepeatAfterDays | None = None
    results_per_query: ResultsPerQuery | None = None
    pages_per_query: PagesPerQuery | None = None
    use_job_feeds: bool | None = None
    feed_items: FeedItems | None = None
    query_suffix: QuerySuffix | None = None


class OverlayQualification(_Strict):
    min_qualify: Score | None = None
    min_draft: Score | None = None
    min_confidence: Confidence | None = None
    accept_hiring_signals: bool | None = None
    accept_service_companies: bool | None = None
    max_age_days: MaxAgeDays | None = None
    min_relevance_terms: RelevanceTerms | None = None


class OverlayLimits(_Strict):
    max_new_leads_per_run: MaxNewLeads | None = None
    max_pages_per_run: MaxPagesPerRun | None = None
    max_pages_per_domain: MaxPagesPerDomain | None = None
    engine_rest_minutes: EngineRestMinutes | None = None


class CampaignOverlay(_Strict):
    config_version: Literal["1.0"]
    project_context: OverlayProjectContext = OverlayProjectContext()
    search: OverlaySearch = OverlaySearch()
    qualification: OverlayQualification = OverlayQualification()
    # Added to the base lists, never replacing them (merge semantics: M3).
    exclude_terms: TermList = []
    exclude_companies: TermList = []
    exclude_countries: TermList = []
    exclude_tlds: Annotated[list[Tld], Field(max_length=30)] = []
    # Replaces (it is not added to anything): the Marketing Agent rejects a company it can place outside these countries.
    include_countries: TermList = []
    limits: OverlayLimits = OverlayLimits()

    @model_validator(mode="after")
    def _capabilities_in_base(self) -> "CampaignOverlay":
        if self.search.capabilities is not None:
            from campaign_agent.contract.vocabularies import base_capabilities

            unknown = sorted(set(self.search.capabilities) - set(base_capabilities()))
            if unknown:
                raise ValueError(f"search.capabilities not in the base list: {unknown}")
        return self

    @model_validator(mode="after")
    def _pair_rules(self) -> "CampaignOverlay":
        # Only rules whose fields are all present. The full check runs on the merged config (M3).
        q, s, li = self.qualification, self.search, self.limits
        check_rules(
            min_qualify=q.min_qualify,
            min_draft=q.min_draft,
            queries_per_run=s.queries_per_run,
            pages_per_query=s.pages_per_query,
            results_per_query=s.results_per_query,
            max_pages_per_run=li.max_pages_per_run,
        )
        return self


@dataclass(frozen=True)
class RuleViolation:
    code: str
    path: str  # the key to change
    message: str


def rule_violations(
    *,
    min_qualify: int | None,
    min_draft: int | None,
    queries_per_run: int | None,
    pages_per_query: int | None,
    results_per_query: int | None,
    max_pages_per_run: int | None,
) -> list[RuleViolation]:
    """Cross-field rules from changes doc A6. A rule is skipped when one of its fields is None."""
    found: list[RuleViolation] = []
    if min_qualify is not None and min_draft is not None and min_draft < min_qualify:
        found.append(
            RuleViolation(
                "draft_below_qualify",
                "qualification.min_draft",
                f"qualification.min_draft ({min_draft}) must be >= min_qualify ({min_qualify})",
            )
        )
    if (
        results_per_query is not None
        and pages_per_query is not None
        and results_per_query < pages_per_query
    ):
        found.append(
            RuleViolation(
                "results_below_pages",
                "search.results_per_query",
                f"search.results_per_query ({results_per_query}) must be >= "
                f"pages_per_query ({pages_per_query})",
            )
        )
    if (
        queries_per_run is not None
        and pages_per_query is not None
        and max_pages_per_run is not None
        and max_pages_per_run < queries_per_run * pages_per_query
    ):
        found.append(
            RuleViolation(
                "pages_starved",
                "limits.max_pages_per_run",
                f"limits.max_pages_per_run ({max_pages_per_run}) must be >= queries_per_run x "
                f"pages_per_query ({queries_per_run * pages_per_query}), "
                "or late queries are starved",
            )
        )
    return found


def check_rules(**values: int | None) -> None:
    found = rule_violations(**values)
    if found:
        raise ValueError("; ".join(v.message for v in found))
