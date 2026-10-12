import pytest

from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import PatchOp, Suggestion
from campaign_agent.ir.provenance import Status
from campaign_agent.merger.patch_merger import (
    Reason,
    Source,
    SuggestionResolvedError,
    TurnContext,
    UnknownSuggestionError,
    add_suggestions,
    merge_patch,
    resolve_suggestion,
)

URL = "https://example.org/what-is-aosp"


def sug(path: str = "audience.products", value: object = "digital cockpit") -> Suggestion:
    return Suggestion(path=path, value=value, sources=[URL], rationale="seen in docs")  # type: ignore[arg-type]


@pytest.fixture
def ir() -> CampaignIR:
    return new_ir("c1")


def test_suggestions_never_change_values(ir: CampaignIR) -> None:
    r = add_suggestions(ir, [sug()], turn=2)
    assert r.ir.audience.products == []
    assert "audience.products" not in r.ir.provenance
    assert r.ir.suggestions[0].status == "suggested"
    assert r.ir.suggestions[0].sources == (URL,)
    assert r.ir.meta.revision == 1


def test_only_concept_fields_can_be_suggested(ir: CampaignIR) -> None:
    for path in (
        "qualification.min_qualify",
        "exclusions.companies",
        "approval.required",
        "limits.queries_per_run",
    ):
        r = add_suggestions(ir, [sug(path, 5)], turn=2)
        assert r.rejected[0].reason is Reason.NOT_SUGGESTIBLE
        assert r.ir.suggestions == []


def test_invalid_suggestion_values_are_dropped(ir: CampaignIR) -> None:
    r = add_suggestions(ir, [sug(value="x\ny")], turn=2)
    assert r.rejected[0].reason is Reason.INVALID_VALUE


def test_duplicates_and_present_values_are_skipped(ir: CampaignIR) -> None:
    once = add_suggestions(ir, [sug()], turn=2).ir
    again = add_suggestions(once, [sug()], turn=3)
    assert again.rejected[0].reason is Reason.DUPLICATE_SUGGESTION
    has = merge_patch(
        ir,
        __import__("campaign_agent.ir.patch", fromlist=["IRPatch"]).IRPatch(
            ops=[PatchOp(path="audience.products", op="add", value="kiosk")]
        ),
        TurnContext(1, Source.DIRECT),
    ).ir
    skipped = add_suggestions(has, [sug(value="Kiosk")], turn=2)
    assert skipped.rejected[0].reason is Reason.NO_CHANGE


def test_accepting_confirms_the_value_with_its_sources(ir: CampaignIR) -> None:
    ir2 = add_suggestions(ir, [sug()], turn=2).ir
    sid = ir2.suggestions[0].id
    r = resolve_suggestion(ir2, sid, "accept", turn=3)
    assert r.ir.audience.products == ["digital cockpit"]
    prov = r.ir.provenance["audience.products"]
    assert prov.status is Status.CONFIRMED
    assert prov.sources == (URL,)
    assert r.ir.suggestions[0].status == "accepted"


def test_rejecting_keeps_values_and_blocks_a_repeat(ir: CampaignIR) -> None:
    ir2 = add_suggestions(ir, [sug()], turn=2).ir
    r = resolve_suggestion(ir2, ir2.suggestions[0].id, "reject", turn=3)
    assert r.ir.audience.products == []
    assert r.ir.suggestions[0].status == "rejected"
    assert add_suggestions(r.ir, [sug()], turn=4).rejected[0].reason is Reason.DUPLICATE_SUGGESTION


def test_a_suggestion_cannot_be_resolved_twice_or_unknown(ir: CampaignIR) -> None:
    ir2 = add_suggestions(ir, [sug()], turn=2).ir
    sid = ir2.suggestions[0].id
    done = resolve_suggestion(ir2, sid, "accept", turn=3).ir
    with pytest.raises(SuggestionResolvedError):
        resolve_suggestion(done, sid, "reject", turn=4)
    with pytest.raises(UnknownSuggestionError):
        resolve_suggestion(ir2, "s_nope", "accept", turn=3)


def test_list_suggestions_only_store_the_new_items(ir: CampaignIR) -> None:
    ir2 = merge_patch(
        ir,
        __import__("campaign_agent.ir.patch", fromlist=["IRPatch"]).IRPatch(
            ops=[PatchOp(path="audience.products", op="add", value="kiosk")]
        ),
        TurnContext(1, Source.DIRECT),
    ).ir
    r = add_suggestions(ir2, [sug(value=["kiosk", "digital cockpit"])], turn=2)
    assert r.ir.suggestions[0].value == ["digital cockpit"]


def test_suggestion_cap(ir: CampaignIR) -> None:
    sugs = [sug(value=f"product {i}") for i in range(25)]
    r = add_suggestions(ir, sugs, turn=2)
    assert len(r.ir.suggestions) == 20
    assert r.rejected[-1].reason is Reason.TOO_MANY_SUGGESTIONS
