"""The benchmark cases: what the user said, and what a correct reading of it looks like.

A case is a short conversation (one or more turns) against an optional starting campaign. The gold answer is a list of
operations per turn, each with the quote that justifies it. Scoring compares the CHANGES the model made to the
campaign with the changes the gold operations make, so there are several right ways to say the same thing.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "complete", "ambiguous", "contradictory", "unsupported", "adversarial", "multi_turn", "edit"
]
Split = Literal["dev", "heldout"]
Expected = Literal["ok", "input_rejected"]

CATEGORIES: tuple[str, ...] = (
    "complete",
    "ambiguous",
    "contradictory",
    "unsupported",
    "adversarial",
    "multi_turn",
    "edit",
)


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoldOp(_C):
    path: str
    op: Literal["set", "add", "remove"] = "add"
    value: Any
    quote: str = ""


class GoldUnsupported(_C):
    kind: Literal["channel", "approval", "outreach_content", "other"]
    request: str


class GoldResearch(_C):
    """What the model should ask to look up, and which suggested values would be good ones."""

    kind: Literal["term_meaning", "product_categories_for_technology", "industries_for_capability"]
    term: str
    good: list[str] = Field(default_factory=list)


class Turn(_C):
    text: str
    source: Literal["message", "edit"] = "message"
    ops: list[GoldOp] = Field(default_factory=list)
    unsupported: list[GoldUnsupported] = Field(default_factory=list)


class Case(_C):
    id: str
    category: Category
    split: Split
    turns: list[Turn] = Field(min_length=1)
    base: list[GoldOp] = Field(default_factory=list)  # set directly (confirmed) before turn 1
    must_ask: list[str] = Field(default_factory=list)  # gap reasons expected after the last turn
    forbidden_paths: list[str] = Field(default_factory=list)  # path prefixes that must stay untouched
    expect_status: Expected = "ok"
    research: list[GoldResearch] = Field(default_factory=list)
    note: str = ""


def load_cases(path: Path) -> list[Case]:
    cases = [
        Case.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases


def select(cases: list[Case], split: str) -> list[Case]:
    if split == "all":
        return list(cases)
    return [c for c in cases if c.split == split]


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
