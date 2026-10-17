"""Level 6 against the REAL Marketing Agent, not the stand-in in tests/e2e/fake_ma (audit 2026-10-17, F-03 and F-25).

The stand-in implemented `leads queries --campaign FILE`; the real Marketing Agent did not, and nothing noticed. CI checks
out the Marketing Agent (job "Contract with the real Marketing Agent") and runs this file with:
    REAL_MA_CHECKOUT   the checkout
    REAL_MA_PYTHON     the Python of its virtual environment
Skipped when they are not set.
"""

import os
from pathlib import Path

import pytest

from campaign_agent.compiler.build import build_candidate
from campaign_agent.validators.downstream import Downstream
from tests.unit.helpers import ir_with

pytestmark = pytest.mark.e2e

CHECKOUT = os.environ.get("REAL_MA_CHECKOUT", "")
PYTHON = os.environ.get("REAL_MA_PYTHON", "")


@pytest.fixture
def real_ma() -> Downstream:
    if not CHECKOUT or not PYTHON:
        pytest.skip(
            "set REAL_MA_CHECKOUT and REAL_MA_PYTHON to check against the real Marketing Agent"
        )
    # No network isolation here: a CI machine may not allow network namespaces. The service always isolates.
    return Downstream(
        checkout=Path(CHECKOUT), python=PYTHON, timeout_s=120, required=True, isolate=()
    )


def test_the_real_marketing_agent_accepts_a_compiled_campaign(real_ma: Downstream) -> None:
    candidate = build_candidate(
        ir_with(
            ("audience.industries", ["automotive"]),
            ("audience.products", ["digital cockpit"]),
            ("audience.technologies", ["Android Automotive"]),
            ("audience.include_countries", ["Germany", "Austria"]),
            ("exclusions.countries", ["France"]),
            ("contacts.preferred_roles", ["cto", "procurement"]),
            name="Real MA contract",
        )
    )
    result = real_ma.check(candidate.yaml)
    assert result.status == "passed", [i.message for i in result.issues]


def test_the_real_marketing_agent_refuses_a_key_a_campaign_may_not_set(real_ma: Downstream) -> None:
    result = real_ma.check("config_version: '1.0'\nsending:\n  approve: false\n")
    assert result.status == "failed"
    assert any(i.code == "loader_rejected" for i in result.issues)
