import yaml

from campaign_agent.compiler.build import build_candidate
from campaign_agent.contract.loader import load_overlay
from campaign_agent.serializer.yaml_serializer import (
    dump_body,
    header,
    serialize_overlay,
    strip_header,
)
from tests.unit.helpers import ir_with

IR = ir_with(
    ("audience.industries", ["automotive"]),
    ("audience.products", ["digital cockpit"]),
    ("qualification.min_qualify", 60),
    ("exclusions.companies", ["acme"]),
    ("limits.max_new_leads_per_run", 5),
)


def test_same_ir_gives_byte_identical_yaml_and_hash() -> None:
    a, b = build_candidate(IR), build_candidate(IR)
    assert a.yaml == b.yaml
    assert a.sha256 == b.sha256


def test_keys_follow_the_contract_order() -> None:
    keys = list(yaml.safe_load(build_candidate(IR).yaml))
    assert keys == [
        "config_version",
        "project_context",
        "qualification",
        "exclude_companies",
        "limits",
    ]


def test_output_is_valid_under_the_contract() -> None:
    assert load_overlay(build_candidate(IR).yaml).qualification.min_qualify == 60


def test_no_anchors_or_aliases_even_for_a_shared_list() -> None:
    shared = ["a", "b"]
    text = dump_body(
        {"config_version": "1.0", "exclude_terms": shared, "exclude_companies": shared}
    )
    assert "&" not in text
    assert "*" not in text


def test_header_has_no_timestamp_and_sanitises_the_id() -> None:
    head = header(
        ir_hash="h" * 64, campaign_id="x\n# injected: yes", contract_version="1.0", not_applied=2
    )
    assert head.count("\n") == 5
    assert all(line.startswith("#") for line in head.splitlines())
    assert "injected" not in head.replace("?", "") or "\n# injected" not in head
    assert "2 request(s)" in head


def test_header_is_stripped_before_parsing() -> None:
    text = serialize_overlay(
        {"config_version": "1.0"},
        ir_hash="h" * 64,
        campaign_id="c1",
        contract_version="1.0",
        not_applied=0,
    )
    assert strip_header(text) == dump_body({"config_version": "1.0"})


def test_user_text_is_quoted_not_templated() -> None:
    text = dump_body({"config_version": "1.0", "project_context": {"name": "yes: no # x"}})
    assert yaml.safe_load(text)["project_context"]["name"] == "yes: no # x"
