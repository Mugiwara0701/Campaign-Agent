"""Test-run helpers shared by all tests."""

import os
from typing import Any


def pytest_runtest_logreport(report: Any) -> None:
    """On GitHub Actions, report each failing test as an annotation (file, line and the reason), so a failed
    run can be read from the checks API without opening the log."""
    if not (report.failed and os.environ.get("GITHUB_ACTIONS") == "true"):
        return
    crash = getattr(report.longrepr, "reprcrash", None)
    message = str(crash.message if crash else report.longrepr)[:500]
    line = crash.lineno if crash else 1
    path = str(crash.path if crash else report.fspath)
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = str(report.nodeid).replace(",", " ").replace(":", " ")
    print(f"\n::error file={path},line={line},title={title}::{message}")
