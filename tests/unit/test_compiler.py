from campaign_agent.compiler.compiler import (
    EXCLUDE_KEYS,
    compile_overlay,
    effective_config_hash,
    merge_onto_base,
)
from campaign_agent.contract.loader import load_full
from campaign_agent.contract.model import CampaignOverlay, LeadgenFile
from campaign_agent.contract.vocabularies import base_config
from campaign_agent.ir.models import new_ir
from campaign_agent.merger.patch_merger import apply_defaults
from tests.unit.helpers import ir_with


def test_empty_ir_compiles_to_the_version_and_the_name_only() -> None:
    assert compile_overlay(new_ir("c1")).data == {"config_version": "1.0"}


def test_audience_maps_to_project_context() -> None:
    ir = ir_with(
        ("audience.industries", ["automotive"]),
        ("audience.products", ["digital cockpit"]),
        ("audience.technologies", ["Android Automotive"]),
        ("audience.capabilities_needed", ["AOSP customization"]),
    )
    pc = compile_overlay(ir).data["project_context"]
    assert pc == {
        "name": "Test campaign",
        "industries": ["automotive"],
        "products": ["digital cockpit"],
        "technologies": ["Android Automotive"],
        "engineering_requirements": ["AOSP customization"],
    }


def test_search_capabilities_are_never_emitted() -> None:
    ir = ir_with(("audience.capabilities_needed", ["Android HAL"]))
    assert "capabilities" not in compile_overlay(ir).data.get("search", {})


def test_volume_goes_to_search_and_caps_to_limits() -> None:
    data = compile_overlay(
        ir_with(
            ("limits.queries_per_run", 20),
            ("limits.max_new_leads_per_run", 5),
            ("limits.max_pages_per_run", 150),
        )
    ).data
    assert data["search"] == {"queries_per_run": 20}
    assert data["limits"] == {"max_new_leads_per_run": 5, "max_pages_per_run": 150}


def test_qualification_and_exclusions() -> None:
    data = compile_overlay(
        ir_with(
            ("qualification.min_qualify", 60),
            ("exclusions.companies", ["acme"]),
            ("exclusions.tlds", [".fr"]),
        )
    ).data
    assert data["qualification"] == {"min_qualify": 60}
    assert data["exclude_companies"] == ["acme"]
    assert data["exclude_tlds"] == [".fr"]


def test_defaulted_values_are_not_emitted() -> None:
    ir = apply_defaults(ir_with(("qualification.min_qualify", 60))).ir
    assert ir.limits.queries_per_run == 16  # known to the IR...
    assert compile_overlay(ir).data["qualification"] == {
        "min_qualify": 60
    }  # ...but not pinned in the overlay
    assert "search" not in compile_overlay(ir).data


def test_the_name_is_sanitised() -> None:
    data = compile_overlay(ir_with(campaign_id="c1", name='Auto\nexclude_companies: []  "x"')).data
    assert "\n" not in data["project_context"]["name"]
    assert '"' not in data["project_context"]["name"]
    CampaignOverlay.model_validate(data)


def test_unsupported_requests_are_reported_not_dropped() -> None:
    ir = ir_with(
        ("audience.include_countries", ["US"]),
        ("audience.employee_min", 500),
        ("audience.employee_max", 5000),
        ("contacts.preferred_roles", ["cto"]),
    )
    result = compile_overlay(ir)
    proposals = {n.proposal for n in result.not_applied}
    assert {"targeting.employee_range", "contacts.preferred_roles"} <= proposals
    assert "targeting.include_countries" not in proposals  # countries are applied now
    text = str(result.data)
    assert "cto" not in text
    assert "employee" not in text
    # the countries are enforced by the Marketing Agent, and the searches lean that way too
    assert result.data["include_countries"] == ["US"]
    assert result.data["search"] == {"query_suffix": "US"}
    countries = next(n for n in result.not_applied if n.request.startswith("audience.include_countries"))
    assert countries.kind == "partially_applied" and countries.reason.startswith("Applied")
    assert "cannot tell is kept" in countries.reason  # the one limit is said


def test_compile_is_pure_and_repeatable() -> None:
    ir = ir_with(("audience.products", ["kiosk"]), ("qualification.min_qualify", 60))
    before = ir.model_dump()
    assert compile_overlay(ir) == compile_overlay(ir)
    assert ir.model_dump() == before


def test_the_overlay_does_not_alias_ir_lists() -> None:
    ir = ir_with(("audience.products", ["kiosk"]))
    data = compile_overlay(ir).data
    data["project_context"]["products"].append("hacked")
    assert ir.audience.products == ["kiosk"]


# --- merge semantics ------------------------------------------------------------------------------------------


def test_overlay_scalars_replace_base_values() -> None:
    base = base_config()
    eff = merge_onto_base({"config_version": "1.0", "search": {"queries_per_run": 20}}, base)
    assert eff["search"]["queries_per_run"] == 20
    assert eff["search"]["pages_per_query"] == base.search.pages_per_query


def test_exclusions_are_added_never_removed() -> None:
    base = base_config()
    eff = merge_onto_base(
        {"config_version": "1.0", "exclude_companies": ["Acme", "NVIDIA", "nuro"]}, base
    )
    assert eff["exclude_companies"][: len(base.exclude_companies)] == base.exclude_companies
    assert eff["exclude_companies"][-1] == "Acme"
    assert (
        len(eff["exclude_companies"]) == len(base.exclude_companies) + 1
    )  # nvidia and nuro already there
    for key in EXCLUDE_KEYS:
        assert set(getattr(base, key)) <= set(eff[key])


def test_merge_does_not_touch_the_base() -> None:
    base = base_config()
    snapshot = base.model_dump()
    merge_onto_base(
        {"config_version": "1.0", "exclude_terms": ["x"], "project_context": {"products": ["a"]}},
        base,
    )
    assert base.model_dump() == snapshot


def test_effective_config_loads_as_a_full_file() -> None:
    import yaml

    eff = merge_onto_base(
        compile_overlay(ir_with(("audience.products", ["kiosk"]))).data, base_config()
    )
    LeadgenFile.model_validate(eff)
    load_full(yaml.safe_dump(eff))


def test_effective_hash_is_stable_and_changes_with_content() -> None:
    base = base_config()
    a = merge_onto_base({"config_version": "1.0"}, base)
    b = merge_onto_base({"config_version": "1.0", "search": {"queries_per_run": 20}}, base)
    assert effective_config_hash(a) == effective_config_hash(
        merge_onto_base({"config_version": "1.0"}, base)
    )
    assert effective_config_hash(a) != effective_config_hash(b)


def test_several_countries_become_one_search_hint_that_fits_the_contract() -> None:
    data = compile_overlay(
        ir_with(("audience.include_countries", ["Germany", "Austria", "Switzerland"]))
    ).data
    assert data["search"]["query_suffix"] == "Germany Austria Switzerland"
    CampaignOverlay.model_validate(data)
    many = [f"Country{i}" for i in range(30)]
    suffix = compile_overlay(ir_with(("audience.include_countries", many))).data["search"][
        "query_suffix"
    ]
    assert len(suffix) <= 80 and suffix.startswith("Country0 Country1")


def test_a_search_suffix_the_user_set_is_never_replaced_by_the_countries() -> None:
    result = compile_overlay(
        ir_with(("limits.query_suffix", "EV charging"), ("audience.include_countries", ["Germany"]))
    )
    assert result.data["search"]["query_suffix"] == "EV charging"
    assert result.data["include_countries"] == ["Germany"]
    note = next(n for n in result.not_applied if n.request.startswith("audience.include_countries"))
    assert "lean that way" not in note.reason  # it must not claim a hint that was not written


def test_no_countries_means_no_hint() -> None:
    assert "search" not in compile_overlay(ir_with(("audience.products", ["kiosk"]))).data
