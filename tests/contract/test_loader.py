import pytest

from campaign_agent.contract.loader import MAX_BYTES, ContractViolation, load_overlay, parse_yaml


def test_empty_overlay_requires_a_version() -> None:
    with pytest.raises(ContractViolation, match="config_version"):
        load_overlay("{}")


def test_invalid_yaml_is_a_violation_not_a_crash() -> None:
    with pytest.raises(ContractViolation, match="not valid YAML"):
        parse_yaml("a: [unclosed")


def test_oversized_file_is_refused() -> None:
    with pytest.raises(ContractViolation, match="larger than"):
        parse_yaml("a: " + "x" * MAX_BYTES)


def test_python_object_tags_are_not_executed() -> None:
    with pytest.raises(ContractViolation):
        parse_yaml("a: !!python/object/apply:os.system ['echo hi']")


def test_overlay_is_frozen() -> None:
    overlay = load_overlay('config_version: "1.0"')
    with pytest.raises(ValueError, match="frozen"):
        overlay.config_version = "1.0"  # type: ignore[misc]
