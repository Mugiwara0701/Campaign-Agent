from typing import Any

import pytest

from campaign_agent.ir.hashing import ir_hash
from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp, UnsupportedClaim
from campaign_agent.ir.provenance import Status
from campaign_agent.merger.patch_merger import (
    Reason,
    Source,
    TurnContext,
    apply_defaults,
    merge_patch,
)

TEXT = "Find companies building automotive infotainment on Android, minimum score 65, drafts at 60"


def op(path: str, value: Any = None, kind: str = "set", quote: str | None = None) -> PatchOp:
    return PatchOp(path=path, op=kind, value=value, evidence_quote=quote)  # type: ignore[arg-type]


def run(
    ir: CampaignIR,
    *ops: PatchOp,
    source: Source = Source.EXTRACTION,
    turn: int = 1,
    text: str = TEXT,
    claims: list[UnsupportedClaim] | None = None,
):  # type: ignore[no-untyped-def]
    return merge_patch(
        ir, IRPatch(ops=list(ops), unsupported=claims or []), TurnContext(turn, source, text)
    )


def reasons(result) -> list[Reason]:  # type: ignore[no-untyped-def]
    return [r.reason for r in result.rejected]


@pytest.fixture
def ir() -> CampaignIR:
    return new_ir("c1", "Auto")


# --- provenance ---------------------------------------------------------------------------------------------


def test_extraction_is_inferred_with_a_verified_quote(ir: CampaignIR) -> None:
    r = run(ir, op("audience.industries", ["automotive"], "add", "automotive infotainment"))
    assert r.ir.audience.industries == ["automotive"]
    prov = r.ir.provenance["audience.industries"]
    assert prov.status is Status.INFERRED
    assert prov.evidence_quote == "automotive infotainment"
    assert prov.source_turn == 1


def test_quote_check_ignores_case_and_spacing(ir: CampaignIR) -> None:
    r = run(ir, op("audience.technologies", ["Android"], "add", "ON   ANDROID"))
    assert r.ir.audience.technologies == ["Android"]


def test_model_ops_need_a_quote_and_it_must_be_in_the_text(ir: CampaignIR) -> None:
    r = run(ir, op("audience.industries", ["automotive"], "add"))
    assert reasons(r) == [Reason.EVIDENCE_MISSING]
    r = run(ir, op("audience.industries", ["automotive"], "add", "we saw this on a website"))
    assert reasons(r) == [Reason.EVIDENCE_NOT_FOUND]
    assert r.ir == ir


def test_user_sources_confirm_without_a_quote(ir: CampaignIR) -> None:
    for source in (Source.DIRECT, Source.ANSWER):
        r = run(ir, op("qualification.min_qualify", 70), source=source)
        assert r.ir.provenance["qualification.min_qualify"].status is Status.CONFIRMED


def test_edit_request_confirms_when_the_quote_is_real(ir: CampaignIR) -> None:
    r = run(
        ir, op("qualification.min_draft", 65, quote="minimum score 65"), source=Source.EDIT_REQUEST
    )
    assert r.ir.provenance["qualification.min_draft"].status is Status.CONFIRMED


# --- locks ------------------------------------------------------------------------------------------------


def test_extraction_cannot_overwrite_a_confirmed_value(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT).ir
    r = run(ir, op("qualification.min_qualify", 60, quote="drafts at 60"))
    assert reasons(r) == [Reason.LOCKED]
    assert r.rejected[0].kept_value == 70
    assert r.ir.qualification.min_qualify == 70


def test_whole_list_is_locked_once_confirmed(ir: CampaignIR) -> None:
    ir = run(ir, op("exclusions.companies", ["acme"], "add"), source=Source.DIRECT).ir
    r = run(ir, op("exclusions.companies", ["automotive"], "add", "automotive infotainment"))
    assert reasons(r) == [Reason.LOCKED]
    assert r.ir.exclusions.companies == ["acme"]


def test_user_can_correct_a_confirmed_value(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT).ir
    for source in (Source.DIRECT, Source.ANSWER):
        r = run(ir, op("qualification.min_qualify", 55), source=source)
        assert r.ir.qualification.min_qualify == 55
    r = run(
        ir,
        op("qualification.min_qualify", 60, quote="drafts at 60"),
        source=Source.EDIT_REQUEST,
    )
    assert r.ir.qualification.min_qualify == 60


def test_extraction_can_update_an_inferred_value(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 60, quote="drafts at 60")).ir
    r = run(ir, op("qualification.min_qualify", 65, quote="minimum score 65"), turn=2)
    assert r.ir.qualification.min_qualify == 65
    assert r.ir.provenance["qualification.min_qualify"].source_turn == 2


# --- null and clear ---------------------------------------------------------------------------------------


def test_null_from_the_model_means_missing(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT).ir
    r = run(ir, op("qualification.min_qualify", None, quote="minimum score 65"))
    assert reasons(r) == [Reason.NULL_IGNORED]
    assert r.ir.qualification.min_qualify == 70


def test_only_the_user_can_clear(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT).ir
    r = run(ir, op("qualification.min_qualify", kind="clear", quote="minimum score 65"))
    assert reasons(r) == [Reason.CLEAR_NOT_ALLOWED]
    assert r.ir.qualification.min_qualify == 70
    r = run(
        ir, op("qualification.min_qualify", None), source=Source.DIRECT
    )  # null = clear for the user
    assert r.ir.qualification.min_qualify is None
    assert "qualification.min_qualify" not in r.ir.provenance


def test_clear_on_an_unset_field_is_no_change(ir: CampaignIR) -> None:
    r = run(ir, op("exclusions.terms", kind="clear"), source=Source.DIRECT)
    assert reasons(r) == [Reason.NO_CHANGE]
    assert r.ir.meta.revision == 0


# --- lists --------------------------------------------------------------------------------------------------


def test_list_add_remove_and_dedupe(ir: CampaignIR) -> None:
    r = run(
        ir,
        op("audience.products", ["kiosk", "Kiosk", "digital cockpit"], "add"),
        source=Source.DIRECT,
    )
    assert r.ir.audience.products == ["kiosk", "digital cockpit"]
    r = run(r.ir, op("audience.products", "KIOSK", "remove"), source=Source.DIRECT)
    assert r.ir.audience.products == ["digital cockpit"]
    r = run(r.ir, op("audience.products", ["a", "b"], "set"), source=Source.DIRECT)
    assert r.ir.audience.products == ["a", "b"]


def test_adding_a_present_item_is_no_change(ir: CampaignIR) -> None:
    ir = run(ir, op("audience.products", ["kiosk"], "add"), source=Source.DIRECT).ir
    r = run(ir, op("audience.products", "kiosk", "add"), source=Source.DIRECT)
    assert reasons(r) == [Reason.NO_CHANGE]


def test_add_or_remove_on_a_single_value_is_refused(ir: CampaignIR) -> None:
    r = run(ir, op("qualification.min_qualify", 5, "add"), source=Source.DIRECT)
    assert reasons(r) == [Reason.WRONG_OP]


# --- validation ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("qualification.min_confidence", 1.5),
        ("qualification.min_qualify", "70"),
        ("qualification.accept_hiring_signals", "yes"),
        ("limits.queries_per_run", 500),
        ("limits.queries_per_run", True),
        ("contacts.preferred_roles", ["ceo"]),
        ("audience.industries", ["auto\nexclude_companies: []"]),
        ("audience.products", ["{{ payload }}"]),
        ("exclusions.tlds", ["com"]),
        ("audience.employee_min", 0),
    ],
)
def test_invalid_values_are_rejected_and_nothing_changes(
    ir: CampaignIR, path: str, value: Any
) -> None:
    r = run(ir, op(path, value, "add" if isinstance(value, list) else "set"), source=Source.DIRECT)
    assert reasons(r) == [Reason.INVALID_VALUE]
    assert r.ir == ir


def test_roles_come_from_a_controlled_vocabulary(ir: CampaignIR) -> None:
    r = run(ir, op("contacts.preferred_roles", ["cto", "vp_engineering"]), source=Source.DIRECT)
    assert r.ir.contacts.preferred_roles == ["cto", "vp_engineering"]


def test_conflicting_cross_field_values_are_not_the_mergers_job(ir: CampaignIR) -> None:
    r = run(
        ir,
        op("qualification.min_qualify", 80),
        op("qualification.min_draft", 60),
        source=Source.DIRECT,
    )
    assert not r.rejected  # a conflict is a question (M5) or an L4 error (M3)


# --- unsupported and fixed fields -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["approval.required", "approval", "approval.enabled"])
def test_approval_can_never_be_changed(ir: CampaignIR, path: str) -> None:
    for source in Source:
        r = run(ir, op(path, False, quote="minimum score 65"), source=source)
        assert reasons(r) == [Reason.APPROVAL_FIXED]
        assert r.ir.approval.required is True
        assert r.ir.unsupported[0].kind == "approval"


def test_unknown_paths_become_unsupported_items(ir: CampaignIR) -> None:
    r = run(ir, op("targeting.include_countries", ["US"], "add", "automotive infotainment"))
    assert reasons(r) == [Reason.UNKNOWN_PATH]
    assert r.ir.unsupported[0].kind == "other"
    assert "targeting.include_countries" in r.ir.unsupported[0].request
    assert r.ir.meta.revision == 1


def test_system_fields_are_not_patchable(ir: CampaignIR) -> None:
    for path in (
        "meta.id",
        "meta.revision",
        "meta.contract_version",
        "objective.type",
        "provenance",
    ):
        r = run(ir, op(path, "x"), source=Source.DIRECT)
        assert reasons(r) in ([Reason.NOT_PATCHABLE], [Reason.UNKNOWN_PATH])
        assert r.ir.meta.id == "c1"


def test_unsupported_claims_are_recorded_once(ir: CampaignIR) -> None:
    claim = UnsupportedClaim(request="also message them on LinkedIn", kind="channel")
    r = run(ir, claims=[claim])
    r = run(r.ir, claims=[claim, claim])
    assert [u.kind for u in r.ir.unsupported] == ["channel"]


def test_organisation_statements_are_kept_but_not_emitted(ir: CampaignIR) -> None:
    r = run(
        ir,
        op("organisation_context", ["we are a 200-person BSP firm"], "add"),
        source=Source.DIRECT,
    )
    assert r.ir.organisation_context == ["we are a 200-person BSP firm"]


# --- purity and revision ---------------------------------------------------------------------------------------


def test_merge_does_not_modify_the_input(ir: CampaignIR) -> None:
    before = ir.model_dump()
    run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT)
    assert ir.model_dump() == before


def test_revision_goes_up_once_per_changing_merge(ir: CampaignIR) -> None:
    r = run(
        ir,
        op("qualification.min_qualify", 70),
        op("qualification.min_draft", 80),
        source=Source.DIRECT,
    )
    assert r.ir.meta.revision == 1
    same = run(r.ir, op("qualification.min_qualify", 70), source=Source.DIRECT)
    assert same.ir.meta.revision == 1
    assert ir_hash(same.ir) == ir_hash(r.ir)


def test_one_bad_op_does_not_block_the_good_ones(ir: CampaignIR) -> None:
    r = run(
        ir,
        op("qualification.min_qualify", 999),
        op("qualification.min_draft", 70),
        source=Source.DIRECT,
    )
    assert r.ir.qualification.min_draft == 70
    assert reasons(r) == [Reason.INVALID_VALUE]


# --- defaults -----------------------------------------------------------------------------------------------------


def test_defaults_fill_only_unset_fields_and_are_labelled(ir: CampaignIR) -> None:
    ir = run(ir, op("qualification.min_qualify", 70), source=Source.DIRECT).ir
    r = apply_defaults(ir)
    assert r.ir.qualification.min_qualify == 70
    assert r.ir.provenance["qualification.min_qualify"].status is Status.CONFIRMED
    assert r.ir.qualification.min_draft == 60
    assert r.ir.qualification.accept_service_companies is False
    assert r.ir.limits.queries_per_run == 16
    assert r.ir.limits.max_new_leads_per_run == 10
    assert r.ir.provenance["limits.queries_per_run"].status is Status.DEFAULTED


def test_defaults_do_not_touch_lists_or_proposed_fields(ir: CampaignIR) -> None:
    r = apply_defaults(ir)
    assert r.ir.audience.industries == []
    assert r.ir.audience.employee_min is None
    assert apply_defaults(r.ir).ir == r.ir  # idempotent


def test_a_default_can_be_replaced_by_extraction(ir: CampaignIR) -> None:
    ir = apply_defaults(ir).ir
    r = run(ir, op("qualification.min_qualify", 65, quote="minimum score 65"))
    assert r.ir.qualification.min_qualify == 65
    assert r.ir.provenance["qualification.min_qualify"].status is Status.INFERRED


# --- the value must be what the quote says ---------------------------------------------------------------------------


def test_a_real_quote_cannot_justify_an_invented_value() -> None:
    """Seen with a real model: 'Also message them on LinkedIn' was cited for engine_rest_minutes = 60."""
    text = "Find US companies. Also message them on LinkedIn."
    r = run(
        PatchIr(),
        op("limits.engine_rest_minutes", 60, quote="Also message them on LinkedIn"),
        text=text,
    )
    assert reasons(r) == [Reason.VALUE_NOT_IN_QUOTE]
    assert r.ir.limits.engine_rest_minutes is None


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        (500, "500-5,000 employees", True),
        (5000, "500-5,000 employees", True),
        (1000, "500-5,000 employees", False),
        (65, "minimum score 65", True),
        (0.7, "confidence of 70%", True),
        (0.7, "confidence of 0.7", True),
        (0.9, "confidence of 70%", False),
        (500, "five hundred employees", False),  # number words are refused, not guessed
        (["automotive"], "building automotive infotainment", True),
        (["warehouse robot"], "building warehouse robots", True),
        (["Android"], "on ANDROID", True),
        (["in-vehicle infotainment system"], "automotive infotainment", True),
        (["medical"], "building automotive infotainment", False),
        (
            ["automotive", "medical"],
            "building automotive infotainment",
            False,
        ),  # every item needs support
        (["cto"], "find the right decision makers", False),
        (["US"], "Find US companies", True),
        (["US"], "a business that is sustainable", False),  # 'us' inside other words is not a match
        (True, "anything at all", True),  # booleans cannot be checked
    ],
)
def test_value_in_quote(value: Any, quote: str, ok: bool) -> None:
    from campaign_agent.merger.patch_merger import value_in_quote

    assert value_in_quote(value, quote) is ok


def test_user_sources_need_no_grounding() -> None:
    r = run(PatchIr(), op("limits.engine_rest_minutes", 60), source=Source.DIRECT)
    assert r.ir.limits.engine_rest_minutes == 60


def test_a_grounded_op_still_applies() -> None:
    r = run(PatchIr(), op("limits.max_pages_per_run", 65, quote="minimum score 65"))
    assert r.ir.limits.max_pages_per_run == 65  # grounding checks the words, not the meaning


def PatchIr() -> CampaignIR:
    return new_ir("c1")
