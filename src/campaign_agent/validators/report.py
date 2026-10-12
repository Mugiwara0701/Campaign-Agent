"""Result types shared by the validation levels (L3 schema, L4 business, L5 YAML, L6 downstream)."""

from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Fix:
    """The one deterministic change that would clear an issue. Shown to the user, never silent."""

    path: str
    value: Any
    note: str


@dataclass(frozen=True)
class Issue:
    level: int
    code: str
    path: str
    message: str
    severity: Severity = "error"
    fix: Fix | None = None


@dataclass(frozen=True)
class LevelResult:
    level: int
    name: str
    status: Literal["passed", "failed", "skipped"]
    issues: tuple[Issue, ...] = ()
    note: str = ""


def level_result(level: int, name: str, issues: list[Issue]) -> LevelResult:
    failed = any(i.severity == "error" for i in issues)
    return LevelResult(level, name, "failed" if failed else "passed", tuple(issues))


@dataclass(frozen=True)
class ValidationReport:
    levels: tuple[LevelResult, ...]

    @property
    def passed(self) -> bool:
        """No level failed. A skipped level (L6 before M8) is not a failure, but is visible."""
        return all(lv.status != "failed" for lv in self.levels)

    @property
    def issues(self) -> tuple[Issue, ...]:
        return tuple(i for lv in self.levels for i in lv.issues)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")
