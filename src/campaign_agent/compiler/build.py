"""IR -> candidate: compile, serialise, validate L3 to L5. L6 (the MA's loader) arrives in M8."""

from dataclasses import dataclass

from campaign_agent.compiler.compiler import (
    NotApplied,
    compile_overlay,
    effective_config_hash,
    merge_onto_base,
    sha256_text,
)
from campaign_agent.contract.loader import ContractViolation, parse_yaml
from campaign_agent.contract.model import CONFIG_VERSION, LeadgenFile
from campaign_agent.contract.vocabularies import base_config
from campaign_agent.ir.hashing import ir_hash
from campaign_agent.ir.models import CampaignIR
from campaign_agent.serializer.yaml_serializer import serialize_overlay
from campaign_agent.validators.business_rules import check_business_rules
from campaign_agent.validators.report import Issue, LevelResult, ValidationReport, level_result
from campaign_agent.validators.schema import check_schema
from campaign_agent.validators.yaml_check import check_yaml


@dataclass(frozen=True)
class Candidate:
    """What the dashboard reviews.

    `sha256` covers the overlay text exactly as the MA will receive it;
    `effective_hash` identifies the merged configuration a run used (for run reports)."""

    yaml: str
    sha256: str
    ir_hash: str
    effective_hash: str
    contract_version: str
    report: ValidationReport
    not_applied: tuple[NotApplied, ...]

    @property
    def can_freeze(self) -> bool:
        return self.report.passed


def build_candidate(ir: CampaignIR, base: LeadgenFile | None = None) -> Candidate:
    base = base or base_config()
    compiled = compile_overlay(ir)
    digest = ir_hash(ir)
    text = serialize_overlay(
        compiled.data,
        ir_hash=digest,
        campaign_id=ir.meta.id,
        contract_version=CONFIG_VERSION,
        not_applied=len(compiled.not_applied),
    )
    effective = merge_onto_base(compiled.data, base)
    base_dump = base.model_dump(mode="python")
    levels = (
        check_schema(compiled.data, effective),
        check_business_rules(compiled.data, effective, base_dump),
        check_yaml(text),
        LevelResult(
            6, "downstream", "skipped", note="the Marketing Agent's loader is checked from M8"
        ),
    )
    return Candidate(
        yaml=text,
        sha256=sha256_text(text),
        ir_hash=digest,
        effective_hash=effective_config_hash(effective),
        contract_version=CONFIG_VERSION,
        report=ValidationReport(levels),
        not_applied=compiled.not_applied,
    )


def validate_overlay_text(text: str, base: LeadgenFile | None = None) -> ValidationReport:
    """Check a stored overlay again, from its text, against the base config as it is now.

    Used right before publishing: the candidate was valid when it was made, but the base config, the
    contract or the checks may have changed since. Levels 3 to 5; level 6 arrives in M8.
    """
    base = base or base_config()
    skipped = LevelResult(
        6, "downstream", "skipped", note="the Marketing Agent's loader is checked from M8"
    )
    try:
        data = parse_yaml(text)
    except ContractViolation as exc:
        broken = level_result(5, "yaml", [Issue(5, "parse", "(yaml)", str(exc))])
        return ValidationReport((broken, skipped))
    effective = merge_onto_base(data, base)
    return ValidationReport(
        (
            check_schema(data, effective),
            check_business_rules(data, effective, base.model_dump(mode="python")),
            check_yaml(text),
            skipped,
        )
    )
