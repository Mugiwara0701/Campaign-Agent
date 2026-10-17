from typing import Any

import pytest

from campaign_agent.compiler.build import build_candidate
from campaign_agent.compiler.compiler import compile_overlay, merge_onto_base
from campaign_agent.contract.vocabularies import base_config
from campaign_agent.serializer.yaml_serializer import dump_body, serialize_overlay
from campaign_agent.validators.business_rules import check_business_rules
from campaign_agent.validators.schema import check_schema
from campaign_agent.validators.yaml_check import check_yaml
from tests.unit.helpers import ir_with

BASE = base_config()


def l4(overlay: dict[str, Any]):  # type: ignore[no-untyped-def]
    eff = merge_onto_base({"config_version": "1.0", **overlay}, BASE)
    return check_business_rules({"config_version": "1.0", **overlay}, eff, BASE.model_dump())


def codes(level) -> set[str]:  # type: ignore[no-untyped-def]
    return {i.code for i in level.issues}


# --- L3 ---------------------------------------------------------------------------------------------------------


def test_l3_passes_for_a_compiled_overlay() -> None:
    data = compile_overlay(ir_with(("qualification.min_qualify", 60))).data
    assert check_schema(data, merge_onto_base(data, BASE)).status == "passed"


@pytest.mark.parametrize(
    "overlay",
    [
        {"qualification": {"min_qualfy": 70}},
        {"search": {"queries_per_run": 500}},
        {"approval": {"required": False}},
        {"limits": {"host_delay_seconds": 2.0}},
    ],
)
def test_l3_reports_field_errors_with_the_key_path(overlay: dict[str, Any]) -> None:
    data = {"config_version": "1.0", **overlay}
    result = check_schema(data, merge_onto_base({"config_version": "1.0"}, BASE))
    assert result.status == "failed"
    assert result.issues[0].path


def test_l3_leaves_business_rules_to_l4() -> None:
    data = {"config_version": "1.0", "qualification": {"min_qualify": 80, "min_draft": 60}}
    assert check_schema(data, merge_onto_base(data, BASE)).status == "passed"


# --- L4 ---------------------------------------------------------------------------------------------------------


def test_l4_passes_a_clean_overlay() -> None:
    result = l4({"project_context": {"products": ["kiosk"]}, "qualification": {"min_qualify": 55}})
    assert result.status == "passed"
    assert not result.issues


def test_l4_draft_below_qualify_has_no_single_fix() -> None:
    result = l4({"qualification": {"min_qualify": 80, "min_draft": 60}})
    issue = next(i for i in result.issues if i.code == "draft_below_qualify")
    assert result.status == "failed"
    assert issue.fix is None  # raise draft or lower qualify: the user decides


def test_l4_uses_base_values_for_fields_the_overlay_leaves_out() -> None:
    result = l4({"search": {"queries_per_run": 30}})  # base: 6 pages per query, 100 page budget
    issue = next(i for i in result.issues if i.code == "pages_starved")
    assert issue.fix is not None
    assert (issue.fix.path, issue.fix.value) == ("limits.max_pages_per_run", 180)


def test_l4_no_fix_when_the_budget_would_exceed_the_cap() -> None:
    result = l4(
        {
            "search": {"queries_per_run": 40, "pages_per_query": 10},
            "limits": {"max_pages_per_run": 100},
        }
    )
    issue = next(i for i in result.issues if i.code == "pages_starved")
    assert issue.fix is None  # 400 > 300


def test_l4_results_below_pages() -> None:
    assert "results_below_pages" in codes(l4({"search": {"results_per_query": 3}}))


def test_l4_warnings_do_not_fail_the_level() -> None:
    result = l4(
        {
            "search": {"use_job_feeds": True},
            "qualification": {"accept_hiring_signals": False, "accept_service_companies": True},
        }
    )
    assert result.status == "passed"
    assert {"feeds_without_hiring", "service_companies_accepted", "no_audience"} <= codes(result)
    assert all(i.severity == "warning" for i in result.issues)


def test_l4_capabilities_must_come_from_the_base_list() -> None:
    assert "capability_not_in_base" in codes(
        l4({"search": {"capabilities": ["quantum networking"]}})
    )


def test_l4_catches_shrunk_exclusions() -> None:
    eff = merge_onto_base({"config_version": "1.0"}, BASE)
    eff["exclude_companies"] = eff["exclude_companies"][1:]
    result = check_business_rules({"config_version": "1.0"}, eff, BASE.model_dump())
    assert "exclusions_shrunk" in codes(result)


# --- L5 ---------------------------------------------------------------------------------------------------------

GOOD = serialize_overlay(
    {"config_version": "1.0", "exclude_terms": ["x", "y"]},
    ir_hash="h" * 64,
    campaign_id="c1",
    contract_version="1.0",
    not_applied=0,
)


def test_l5_accepts_serializer_output() -> None:
    assert check_yaml(GOOD).status == "passed"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("config_version: '1.0'\na: &x [1]\nb: *x\n", "alias"),
        ("config_version: '1.0'\na: &x 1\n", "anchor"),
        ("config_version: '1.0'\na: !!python/tuple [1]\n", "tag"),
        ("config_version: '1.0'\n---\nconfig_version: '1.0'\n", "multi_document"),
        ("config_version: '1.0'\n\ta: 1\n", "tab"),
        ("config_version: '1.0'\n# sneaky\n", "comment_outside_header"),
        ("a: [unclosed", "syntax"),
        ("x: " + "y" * 300_000, "too_large"),
    ],
)
def test_l5_rejects_unsafe_yaml(text: str, code: str) -> None:
    assert code in codes(check_yaml(text))


def test_l5_rejects_duplicate_keys() -> None:
    assert "parse" in codes(
        check_yaml("config_version: '1.0'\nexclude_terms: [a]\nexclude_terms: [b]\n")
    )


def test_l5_round_trip_catches_a_hand_reformatted_file() -> None:
    flow_style = "config_version: '1.0'\nexclude_terms: [x, y]\n"  # valid YAML, not what the serializer writes
    assert "round_trip" in codes(check_yaml(flow_style))


def test_l5_runs_yamllint() -> None:
    assert "yamllint" in codes(check_yaml("config_version: '1.0'   \n"))  # trailing spaces


def test_l5_catches_a_dump_that_differs_from_the_data() -> None:
    assert check_yaml(GOOD.replace("- x", "- x ")).status == "failed"


# --- the full candidate -----------------------------------------------------------------------------------------


def test_candidate_for_the_example_campaign() -> None:
    ir = ir_with(
        ("audience.industries", ["automotive"]),
        ("audience.products", ["in-vehicle infotainment system", "digital cockpit"]),
        ("audience.technologies", ["Android Automotive"]),
        ("audience.include_countries", ["US"]),
        ("audience.employee_min", 500),
        ("audience.employee_max", 5000),
        ("qualification.min_qualify", 55),
        name="US automotive infotainment",
    )
    c = build_candidate(ir)
    assert c.can_freeze
    assert [(lv.level, lv.status) for lv in c.report.levels] == [
        (3, "passed"),
        (4, "passed"),
        (5, "passed"),
        (6, "skipped"),
    ]
    assert len(c.not_applied) == 3
    assert {n.proposal for n in c.not_applied} == {None, "targeting.employee_range"}
    assert "approval" not in c.yaml
    assert "include_countries" in c.yaml  # applied by the Marketing Agent
    for forbidden in ("targeting", "employee", "500", "5000"):
        assert forbidden not in c.yaml
    assert c.contract_version == "1.0"
    assert len(c.sha256) == len(c.effective_hash) == len(c.ir_hash) == 64


def test_a_failing_candidate_cannot_be_frozen_but_still_has_a_preview() -> None:
    c = build_candidate(ir_with(("limits.queries_per_run", 30)))
    assert not c.can_freeze
    assert c.yaml.startswith("# Campaign overlay")
    assert c.report.errors[0].code == "pages_starved"
    assert c.report.errors[0].fix is not None


def test_changing_the_ir_changes_every_hash() -> None:
    a = build_candidate(ir_with(("qualification.min_qualify", 55)))
    b = build_candidate(ir_with(("qualification.min_qualify", 56)))
    assert (a.sha256, a.ir_hash, a.effective_hash) != (b.sha256, b.ir_hash, b.effective_hash)


def test_an_attempt_to_turn_approval_off_never_reaches_the_yaml() -> None:
    from campaign_agent.ir.models import new_ir
    from campaign_agent.ir.patch import IRPatch, PatchOp
    from campaign_agent.merger.patch_merger import Source, TurnContext, merge_patch

    ir = merge_patch(
        new_ir("c1"),
        IRPatch(ops=[PatchOp(path="approval.required", value=False)]),
        TurnContext(1, Source.DIRECT),
    ).ir
    c = build_candidate(ir)
    assert "approval" not in dump_body(compile_overlay(ir).data)
    assert any(n.kind == "approval" for n in c.not_applied)


# --- supplier wording ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["kiosk makers", "EV charger manufacturer", "OEM controllers", "wholesale kiosks"]
)
def test_l4_rejects_supplier_wording_in_anything_that_becomes_a_query(value: str) -> None:
    result = l4({"project_context": {"products": [value]}})
    assert result.status == "failed"
    issue = next(i for i in result.issues if i.code == "supplier_wording")
    assert issue.path == "project_context.products"
    assert l4({"search": {"query_suffix": "best price"}}).status == "failed"
    assert l4({"project_context": {"industries": [value]}}).status == "failed"


def test_l4_allows_ordinary_words_that_only_contain_supplier_letters() -> None:
    assert (
        l4({"project_context": {"products": ["storefront display", "dealership kiosk"]}}).status
        == "passed"
    )
