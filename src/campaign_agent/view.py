"""What the dashboard shows for a campaign: one JSON document, written to campaign_state.review_view.

The dashboard keeps no copy of campaign truth. It renders this: the fields with where each value came from,
the open questions, web suggestions, what could not be applied, the validation result and the YAML preview.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from campaign_agent.compiler.build import Candidate
from campaign_agent.compiler.compiler import NotApplied
from campaign_agent.conversation.state import ConversationState
from campaign_agent.extractor.prompts import DESCRIPTIONS
from campaign_agent.ir.models import CampaignIR, StoredSuggestion
from campaign_agent.ir.registry import REGISTRY, get_value
from campaign_agent.questions.models import Question
from campaign_agent.validators.report import Issue

EMAIL_APPROVAL = (
    "Every email is approved by a person in Slack before it is sent. A campaign cannot change that."
)

_SECTIONS = (
    ("campaign", "Campaign"),
    ("objective", "Objective"),
    ("audience", "Who to look for"),
    ("qualification", "Qualification"),
    ("contacts", "Contacts"),
    ("exclusions", "Exclusions"),
    ("limits", "Limits"),
    ("organisation_context", "About your company (not applied)"),
)


class _V(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldView(_V):
    path: str
    label: str
    value: JsonValue
    status: str  # confirmed | inferred | defaulted
    evidence_quote: str | None = None
    sources: tuple[str, ...] = ()
    mapping: (
        str  # supported | proposed | not_emitted: proposed means the contract cannot apply it yet
    )


class SectionView(_V):
    name: str
    title: str
    fields: tuple[FieldView, ...]


class IssueView(_V):
    level: int
    code: str
    path: str
    message: str
    severity: str
    fix: dict[str, Any] | None = None


class LevelView(_V):
    level: int
    name: str
    status: str
    note: str = ""
    issues: tuple[IssueView, ...] = ()


class CandidateView(_V):
    version: int | None  # set once the candidate is stored
    sha256: str
    ir_hash: str
    effective_hash: str
    contract_version: str
    yaml: str
    can_freeze: bool
    levels: tuple[LevelView, ...]


class NotAppliedView(_V):
    request: str
    reason: str
    proposal: str | None
    kind: str


class NoticeView(_V):
    code: str
    path: str
    message: str


class LastRequest(_V):
    id: str
    type: str
    outcome: Literal["ok", "failed"]
    code: str | None = None


class ReviewView(_V):
    campaign_id: str
    state: str
    revision: int
    sections: tuple[SectionView, ...]
    questions: tuple[Question, ...]
    suggestions: tuple[StoredSuggestion, ...]
    not_applied: tuple[NotAppliedView, ...]
    notices: tuple[NoticeView, ...]
    warnings: tuple[str, ...]
    candidate: CandidateView | None
    email_approval: str = EMAIL_APPROVAL
    last_request: LastRequest | None = None

    def with_candidate_version(self, version: int) -> "ReviewView":
        assert self.candidate is not None  # noqa: S101
        return self.model_copy(
            update={"candidate": self.candidate.model_copy(update={"version": version})}
        )


def _issue(i: Issue) -> IssueView:
    fix = {"path": i.fix.path, "value": i.fix.value, "note": i.fix.note} if i.fix else None
    return IssueView(
        level=i.level, code=i.code, path=i.path, message=i.message, severity=i.severity, fix=fix
    )


def _candidate(c: Candidate) -> CandidateView:
    levels = tuple(
        LevelView(
            level=lv.level,
            name=lv.name,
            status=lv.status,
            note=lv.note,
            issues=tuple(_issue(i) for i in lv.issues),
        )
        for lv in c.report.levels
    )
    return CandidateView(
        version=None,
        sha256=c.sha256,
        ir_hash=c.ir_hash,
        effective_hash=c.effective_hash,
        contract_version=c.contract_version,
        yaml=c.yaml,
        can_freeze=c.can_freeze,
        levels=levels,
    )


def _sections(ir: CampaignIR) -> tuple[SectionView, ...]:
    out: list[SectionView] = []
    for name, title in _SECTIONS:
        fields: list[FieldView] = []
        for path, spec in REGISTRY.items():
            head = "campaign" if path.startswith("meta.") else path.split(".")[0]
            if head != name:
                continue
            value = get_value(ir, path)
            if value in (None, [], ""):
                continue
            prov = ir.provenance.get(path)
            fields.append(
                FieldView(
                    path=path,
                    label=DESCRIPTIONS[path],
                    value=value,
                    # The name and description are typed by the user in the dashboard, not read by the model.
                    status=prov.status.value
                    if prov
                    else ("confirmed" if head == "campaign" else "inferred"),
                    evidence_quote=prov.evidence_quote if prov else None,
                    sources=prov.sources if prov else (),
                    mapping=spec.mapping,
                )
            )
        if fields:
            out.append(SectionView(name=name, title=title, fields=tuple(fields)))
    return tuple(out)


def build_view(
    ir: CampaignIR,
    conv: ConversationState,
    *,
    candidate: Candidate | None,
    not_applied: tuple[NotApplied, ...],
    notices: tuple[Any, ...] = (),
    warnings: tuple[str, ...] = (),
    last_request: LastRequest | None = None,
) -> ReviewView:
    return ReviewView(
        campaign_id=ir.meta.id,
        state=conv.state.value,
        revision=ir.meta.revision,
        sections=_sections(ir),
        questions=conv.open,
        suggestions=tuple(s for s in ir.suggestions if s.status == "suggested"),
        not_applied=tuple(
            NotAppliedView(request=n.request, reason=n.reason, proposal=n.proposal, kind=n.kind)
            for n in not_applied
        ),
        notices=tuple(NoticeView(code=n.code, path=n.path, message=n.message) for n in notices),
        warnings=warnings,
        candidate=_candidate(candidate) if candidate else None,
        last_request=last_request,
    )
