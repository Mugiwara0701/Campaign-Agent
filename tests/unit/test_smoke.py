"""Keeps CI green while the real modules are still empty."""

import campaign_agent


def test_package_imports() -> None:
    assert campaign_agent is not None
