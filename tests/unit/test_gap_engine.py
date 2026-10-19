from typing import Any

import pytest

from campaign_agent.gap.engine import PAGES_CAP, Kind, Reason, analyze
from campaign_agent.ir.models import new_ir
from campaign_agent.merger.patch_merger import Reason as MergeReason
from campaign_agent.merger.patch_merger import Rejected
from tests.unit.helpers import ir_with


def reasons(findings) -> list[Reason]:  # type: ignore[no-untyped-def]
    return [f.reason for f in findings]


def rejected(path: str, reason: MergeReason) -> Rejected:
    return Rejected(path, "add", reason, "x")


# --- what the campaign is about: the ambiguity cases ---------------------------------------------------------------


def test_an_empty_campaign_asks_for_industry_and_engineering_area() -> None:
    """'Generate a campaign for our services'"""
    found = analyze(new_ir("c1"))
    assert reasons(found) == [Reason.NO_INDUSTRY, Reason.NO_AREA]
    assert {f.kind for f in found} == {Kind.GAP}


def test_an_engineering_area_alone_asks_for_industries() -> None:
    """'Find companies that need embedded engineers'"""
    found = analyze(ir_with(("audience.capabilities_needed", ["embedded Linux"])))
    assert reasons(found) == [Reason.AREA_WITHOUT_AUDIENCE]
    assert found[0].detail["areas"] == ["embedded Linux"]


def test_an_industry_alone_asks_for_the_capability() -> None:
    """'Target automotive companies'"""
    found = analyze(ir_with(("audience.industries", ["automotive"])))
    assert reasons(found) == [Reason.INDUSTRY_WITHOUT_AREA]


def test_a_technology_alone_asks_what_product_they_build() -> None:
    """'Find companies working on Android'"""
    found = analyze(ir_with(("audience.technologies", ["Android"])))
    assert reasons(found) == [Reason.TECH_WITHOUT_PRODUCT]
    assert found[0].path == "audience.products"


def test_a_product_is_enough_to_stop_asking() -> None:
    assert analyze(ir_with(("audience.products", ["EV charger"]))) == []
    assert (
        analyze(ir_with(("audience.industries", ["automotive"]), ("audience.products", ["kiosk"])))
        == []
    )
    full = ir_with(
        ("audience.industries", ["automotive"]),
        ("audience.technologies", ["Android"]),
        ("audience.products", ["digital cockpit"]),
    )
    assert analyze(full) == []


def test_ambiguities_can_stack() -> None:
    ir = ir_with(("audience.industries", ["automotive"]), ("audience.technologies", ["Android"]))
    assert set(reasons(analyze(ir))) == {Reason.INDUSTRY_WITHOUT_AREA, Reason.TECH_WITHOUT_PRODUCT}


# --- conflicts ------------------------------------------------------------------------------------------------------


def test_draft_below_qualify_is_a_conflict_with_both_values() -> None:
    found = analyze(
        ir_with(
            ("qualification.min_qualify", 80),
            ("qualification.min_draft", 60),
            ("audience.products", ["kiosk"]),
        )
    )
    assert reasons(found) == [Reason.DRAFT_BELOW_QUALIFY]
    assert found[0].kind is Kind.CONFLICT
    assert (found[0].detail["min_qualify"], found[0].detail["min_draft"]) == (80, 60)


def test_a_conflict_uses_the_defaults_for_values_nobody_set() -> None:
    found = analyze(
        ir_with(("qualification.min_qualify", 90), ("audience.products", ["kiosk"]))
    )  # default draft is 60
    assert reasons(found) == [Reason.DRAFT_BELOW_QUALIFY]
    assert found[0].detail["min_draft"] == 60


def test_results_below_pages() -> None:
    found = analyze(ir_with(("limits.results_per_query", 3), ("audience.products", ["kiosk"])))
    assert reasons(found) == [Reason.RESULTS_BELOW_PAGES]


def test_page_budget_conflict_carries_a_fix_when_it_fits_the_cap() -> None:
    found = analyze(ir_with(("limits.queries_per_run", 30), ("audience.products", ["kiosk"])))
    assert reasons(found) == [Reason.PAGES_STARVED]
    assert found[0].detail["fix"] == ("limits.max_pages_per_run", 180)
    over = analyze(
        ir_with(
            ("limits.queries_per_run", 40),
            ("limits.pages_per_query", 10),
            ("audience.products", ["kiosk"]),
        )
    )
    assert over[0].detail["needed"] == 400 > PAGES_CAP
    assert over[0].detail["fix"] is None


def test_employee_range_in_the_wrong_order() -> None:
    found = analyze(
        ir_with(
            ("audience.employee_min", 5000),
            ("audience.employee_max", 500),
            ("audience.products", ["kiosk"]),
        )
    )
    assert reasons(found) == [Reason.EMPLOYEE_RANGE]


def test_asking_for_and_excluding_the_same_thing_is_one_conflict_quoting_both() -> None:
    ir = ir_with(
        ("audience.industries", ["automotive"]),
        ("exclusions.terms", ["Automotive", "lidar"]),
        ("audience.products", ["kiosk"]),
    )
    found = analyze(ir)
    assert reasons(found) == [Reason.TARGET_EXCLUDED]
    assert found[0].detail["item"] == "automotive"
    assert found[0].detail["excluded_item"] == "Automotive"
    country = analyze(
        ir_with(
            ("audience.include_countries", ["US"]),
            ("exclusions.countries", ["us"]),
            ("audience.products", ["x"]),
        )
    )
    assert reasons(country) == [Reason.TARGET_EXCLUDED]


def test_a_conflict_that_returns_with_new_values_is_a_new_situation() -> None:
    a = analyze(
        ir_with(
            ("qualification.min_qualify", 80),
            ("qualification.min_draft", 60),
            ("audience.products", ["k"]),
        )
    )
    b = analyze(
        ir_with(
            ("qualification.min_qualify", 90),
            ("qualification.min_draft", 60),
            ("audience.products", ["k"]),
        )
    )
    assert a[0].key != b[0].key


# --- target drift ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["kiosk makers", "EV charger manufacturer", "OEM controllers", "price list"]
)
def test_supplier_wording_is_off_target(value: str) -> None:
    found = analyze(ir_with(("audience.products", [value])))
    assert reasons(found) == [Reason.SUPPLIER_WORDING]
    assert found[0].kind is Kind.DRIFT
    assert found[0].path == "audience.products"


def test_supplier_wording_in_the_query_suffix_and_industries() -> None:
    assert Reason.SUPPLIER_WORDING in reasons(
        analyze(ir_with(("limits.query_suffix", "best price"), ("audience.products", ["kiosk"])))
    )
    assert Reason.SUPPLIER_WORDING in reasons(
        analyze(ir_with(("audience.industries", ["kiosk dealers"]), ("audience.products", ["k"])))
    )


def test_ordinary_words_are_not_drift() -> None:
    assert analyze(ir_with(("audience.products", ["storefront display", "dealership kiosk"]))) == []


# --- rejected values ----------------------------------------------------------------------------------------------------


def test_roles_the_model_could_not_ground_become_one_question_target() -> None:
    """The real-model case: 'find the right decision makers' produced six role guesses."""
    rej = [rejected("contacts.preferred_roles", MergeReason.VALUE_NOT_IN_QUOTE)] * 6
    found = analyze(ir_with(("audience.products", ["kiosk"])), rej)
    assert reasons(found) == [Reason.VALUE_UNCLEAR]
    assert found[0].path == "contacts.preferred_roles"


def test_an_out_of_range_value_is_asked_about_for_any_field() -> None:
    found = analyze(
        ir_with(("audience.products", ["k"])),
        [rejected("qualification.min_qualify", MergeReason.INVALID_VALUE)],
    )
    assert reasons(found) == [Reason.VALUE_UNCLEAR]


def test_an_ungrounded_value_on_an_ordinary_field_is_a_model_slip_not_a_question() -> None:
    """The real-model case: engine_rest_minutes = 60 cited from the LinkedIn sentence."""
    found = analyze(
        ir_with(("audience.products", ["k"])),
        [rejected("limits.engine_rest_minutes", MergeReason.VALUE_NOT_IN_QUOTE)],
    )
    assert found == []


@pytest.mark.parametrize(
    "reason",
    [
        MergeReason.LOCKED,
        MergeReason.NULL_IGNORED,
        MergeReason.NO_CHANGE,
        MergeReason.EVIDENCE_NOT_FOUND,
    ],
)
def test_other_rejections_are_not_questions(reason: MergeReason) -> None:
    assert (
        analyze(ir_with(("audience.products", ["k"])), [rejected("audience.employee_min", reason)])
        == []
    )


# --- preferences -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "we prefer small companies",
        "We usually avoid defence work",
        "our team always wants hardware startups",
    ],
)
def test_an_unscoped_preference_asks_which_campaigns_it_is_for(text: str) -> None:
    ir = ir_with(("audience.products", ["k"]), ("organisation_context", [text]))
    found = analyze(ir)
    assert reasons(found) == [Reason.PREFERENCE_SCOPE]
    assert found[0].detail["statement"] == text


def test_a_plain_statement_about_the_company_is_not_a_preference() -> None:
    assert (
        analyze(
            ir_with(
                ("audience.products", ["k"]),
                ("organisation_context", ["we are a 200-person BSP firm"]),
            )
        )
        == []
    )


# --- order ---------------------------------------------------------------------------------------------------------------


def test_findings_come_most_urgent_first() -> None:
    ir = ir_with(
        ("audience.technologies", ["Android"]),
        ("audience.products", ["kiosk makers"]),
        ("qualification.min_qualify", 80),
        ("qualification.min_draft", 60),
        ("organisation_context", ["we prefer small companies"]),
    )
    kinds = [
        f.kind
        for f in analyze(ir, [rejected("contacts.preferred_roles", MergeReason.VALUE_NOT_IN_QUOTE)])
    ]
    assert kinds == [Kind.CONFLICT, Kind.DRIFT, Kind.UNCLEAR, Kind.PREFERENCE]


def test_findings_are_deterministic() -> None:
    ir: Any = ir_with(
        ("audience.industries", ["automotive"]), ("audience.technologies", ["Android"])
    )
    assert [f.key for f in analyze(ir)] == [f.key for f in analyze(ir)]


def test_a_country_the_base_never_pitches_is_asked_about_at_once() -> None:
    """'Find companies in Germany and Austria': Germany is on the base's do-not-pitch list, so the campaign
    could never pass its checks. The user is asked now, not left with a failed check at the end."""
    found = analyze(
        ir_with(
            ("audience.include_countries", ["Germany", "Austria"]),
            ("audience.products", ["digital cockpit"]),
            ("audience.capabilities_needed", ["embedded Linux"]),
        )
    )
    pitched = [f for f in found if f.reason is Reason.COUNTRY_NOT_PITCHED]
    assert [f.detail["item"] for f in pitched] == ["Germany"]
    assert pitched[0].kind is Kind.CONFLICT
    assert found[0].reason is Reason.COUNTRY_NOT_PITCHED  # conflicts come first


def test_countries_the_base_pitches_raise_nothing() -> None:
    found = analyze(
        ir_with(
            ("audience.include_countries", ["Austria", "Switzerland"]),
            ("audience.products", ["digital cockpit"]),
        )
    )
    assert Reason.COUNTRY_NOT_PITCHED not in reasons(found)
