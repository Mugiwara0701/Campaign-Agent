from pathlib import Path

import pytest

from campaign_agent.contract.loader import ContractViolation, load_overlay

ROOT = Path(__file__).parent.parent / "fixtures" / "leadgen"
VALID = sorted((ROOT / "valid").glob("*.yaml"))
INVALID = sorted((ROOT / "invalid").glob("*.yaml"))


def test_fixtures_exist() -> None:
    assert VALID
    assert len(INVALID) >= 15


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_overlay_loads(path: Path) -> None:
    assert load_overlay(path.read_text(encoding="utf-8")).config_version == "1.0"


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_overlay_fails_with_the_expected_key(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    expected = text.splitlines()[0].removeprefix("# expect:").strip()
    with pytest.raises(ContractViolation) as err:
        load_overlay(text)
    assert expected in str(err.value), f"{path.name}: expected {expected!r} in {err.value}"
