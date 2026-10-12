import pytest
from pydantic import ValidationError

from campaign_agent.ir.hashing import ir_hash
from campaign_agent.ir.models import Approval, CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp, ResearchNeed, Suggestion
from campaign_agent.ir.registry import NOT_PATCHABLE, REGISTRY


def test_new_ir_is_empty_and_has_fixed_approval() -> None:
    ir = new_ir("c1", "Auto", "desc")
    assert ir.meta.revision == 0
    assert ir.approval.required is True
    assert ir.provenance == {}
    assert ir.objective.type == "customer_discovery"


def test_approval_cannot_be_turned_off() -> None:
    with pytest.raises(ValidationError):
        Approval(required=False)  # type: ignore[arg-type]
    data = new_ir("c1").model_dump()
    data["approval"] = {"required": False}
    with pytest.raises(ValidationError):
        CampaignIR.model_validate(data)


def test_ir_rejects_unknown_fields_and_is_frozen() -> None:
    data = new_ir("c1").model_dump()
    data["targeting"] = {"include_countries": ["US"]}
    with pytest.raises(ValidationError):
        CampaignIR.model_validate(data)
    with pytest.raises(ValidationError):
        new_ir("c1").meta.name = "x"  # type: ignore[misc]


def test_ir_round_trips_through_json() -> None:
    ir = new_ir("c1")
    assert CampaignIR.model_validate_json(ir.model_dump_json()) == ir


def test_registry_covers_exactly_the_patchable_value_fields() -> None:
    assert "approval.required" not in REGISTRY
    assert "meta.id" not in REGISTRY
    assert "qualification.min_qualify" in REGISTRY
    assert not set(REGISTRY) & NOT_PATCHABLE
    assert {p for p, s in REGISTRY.items() if s.suggestible} == {
        "audience.industries",
        "audience.products",
        "audience.technologies",
        "audience.capabilities_needed",
    }


def test_patch_schema_is_closed_and_capped() -> None:
    with pytest.raises(ValidationError):
        IRPatch.model_validate({"ops": [], "approval": False})
    with pytest.raises(ValidationError):
        IRPatch.model_validate({"ops": [{"path": "a", "value": 1, "extra": 1}]})
    with pytest.raises(ValidationError):
        IRPatch(ops=[PatchOp(path="a.b")] * 41)
    with pytest.raises(ValidationError):
        IRPatch.model_validate({"research_needs": [{"kind": "find_companies", "term": "x"}]})
    with pytest.raises(ValidationError):
        IRPatch(research_needs=[ResearchNeed(kind="term_meaning", term="a")] * 4)


def test_suggestion_needs_http_sources() -> None:
    with pytest.raises(ValidationError):
        Suggestion(path="audience.products", value="x", sources=[])
    with pytest.raises(ValidationError):
        Suggestion(path="audience.products", value="x", sources=["javascript:alert(1)"])


def test_hash_is_stable_and_ignores_revision() -> None:
    a = new_ir("c1", "x")
    b = a.model_copy(update={"meta": a.meta.model_copy(update={"revision": 9})})
    assert ir_hash(a) == ir_hash(b)
    assert ir_hash(a) != ir_hash(new_ir("c1", "y"))
    assert len(ir_hash(a)) == 64
