"""Turns user text into an IRPatch with one model call (plus bounded repair, retry and fallback).

Budget per job: at most `CallBudget.max_calls` model calls in total, retries included. Order of
resorts when a call goes wrong: retry once on a transient error, step down the output mode if the
backend refuses it, one repair call for invalid output, one call to the fallback model. After that
the user is asked to rephrase. Failed validation is never fed back as "keep going until it passes".
"""

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from campaign_agent.extractor.prompts import system_prompt, user_prompt
from campaign_agent.extractor.schema import closed_patch_schema
from campaign_agent.guard.input_guard import GuardIssue, check_input
from campaign_agent.ir.models import CampaignIR
from campaign_agent.ir.patch import IRPatch
from campaign_agent.ir.registry import REGISTRY
from campaign_agent.llm.adapter import (
    TRANSIENT,
    GenParams,
    LLMAdapter,
    LLMError,
    LLMUnsupportedModeError,
    Message,
    Mode,
    RawResult,
    weaker,
)
from campaign_agent.merger.patch_merger import Source


class Status(StrEnum):
    OK = "ok"
    INPUT_REJECTED = "input_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"
    NEEDS_REPHRASE = "needs_rephrase"
    TOO_LONG = "too_long"
    BUDGET_EXHAUSTED = "budget_exhausted"


class BudgetExhaustedError(Exception):
    pass


@dataclass
class CallBudget:
    max_calls: int = 6
    used: int = 0

    def take(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExhaustedError
        self.used += 1


@dataclass(frozen=True)
class Attempt:
    kind: str  # primary | retry | step_down | repair | fallback
    ok: bool
    mode: Mode
    latency_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str = ""


@dataclass(frozen=True)
class ExtractionResult:
    status: Status
    patch: IRPatch | None = None
    text: str = ""  # the cleaned text the model saw; use it as the evidence text for the merger
    attempts: tuple[Attempt, ...] = ()
    guard_issues: tuple[GuardIssue, ...] = ()
    warnings: tuple[str, ...] = field(default=())


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 3.5)


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_patch_json(raw: str) -> Any:
    """Strict: one JSON object, optionally in a single code fence or after a think block."""
    text = _THINK.sub("", raw).strip()
    if match := _FENCE.match(text):
        text = match.group(1)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("the reply must be a JSON object")
    return value


def normalise_ops(patch: IRPatch) -> tuple[IRPatch, int]:
    """Small models often write op "add" for one value. That is unambiguous: read it as "set"."""
    fixed = 0
    ops = []
    for op in patch.ops:
        spec = REGISTRY.get(op.path)
        if op.op == "add" and spec is not None and spec.kind == "scalar":
            op = op.model_copy(update={"op": "set"})
            fixed += 1
        ops.append(op)
    return patch.model_copy(update={"ops": ops}), fixed


class Extractor:
    def __init__(
        self,
        adapter: LLMAdapter,
        *,
        fallback: LLMAdapter | None = None,
        mode: Mode | None = None,
        params: GenParams | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._fallback = fallback
        self._forced_mode = mode
        self._params = params or GenParams()
        self._sleep = sleep
        self._schema = closed_patch_schema()

    def _start_mode(self, adapter: LLMAdapter) -> Mode:
        caps = adapter.capabilities()
        if self._forced_mode and caps.supports(self._forced_mode):
            return self._forced_mode
        return caps.strongest()

    def _messages(
        self, text: str, ir: CampaignIR | None, is_edit: bool, max_context: int
    ) -> tuple[list[Message], list[str]]:
        warnings: list[str] = []
        system = system_prompt()
        budget = max_context - self._params.max_tokens
        user = user_prompt(text, ir, is_edit=is_edit)
        if estimate_tokens(system + user) > budget and is_edit:
            user = user_prompt(text, ir, is_edit=is_edit, include_state=False)
            warnings.append("the current campaign was left out of the prompt to fit the context")
        return [Message("system", system), Message("user", user)], warnings

    def run(
        self,
        text: str,
        ir: CampaignIR | None = None,
        *,
        source: Source = Source.EXTRACTION,
        budget: CallBudget | None = None,
    ) -> ExtractionResult:
        is_edit = source is Source.EDIT_REQUEST
        guard = check_input(text, "edit" if is_edit else "message")
        if not guard.ok:
            return ExtractionResult(Status.INPUT_REJECTED, guard_issues=guard.issues)
        cleaned = guard.text
        budget = budget or CallBudget()
        max_context = self._adapter.capabilities().max_context
        messages, warnings = self._messages(cleaned, ir, is_edit, max_context)
        if (
            estimate_tokens(" ".join(m.content for m in messages))
            > max_context - self._params.max_tokens
        ):
            return ExtractionResult(Status.TOO_LONG, text=cleaned, warnings=tuple(warnings))

        attempts: list[Attempt] = []
        try:
            patch = self._with_repair(self._adapter, messages, budget, attempts, "primary")
            if patch is None and self._fallback is not None:
                patch = self._with_repair(
                    self._fallback, messages, budget, attempts, "fallback", repair=False
                )
        except BudgetExhaustedError:
            return ExtractionResult(Status.BUDGET_EXHAUSTED, text=cleaned, attempts=tuple(attempts))
        except LLMError:
            return ExtractionResult(
                Status.MODEL_UNAVAILABLE,
                text=cleaned,
                attempts=tuple(attempts),
                warnings=tuple(warnings),
            )
        if patch is None:
            return ExtractionResult(
                Status.NEEDS_REPHRASE,
                text=cleaned,
                attempts=tuple(attempts),
                warnings=tuple(warnings),
            )
        patch, fixed = normalise_ops(patch)
        if fixed:
            warnings.append(f"{fixed} op(s) used add on a single value; read as set")
        return ExtractionResult(
            Status.OK, patch, cleaned, tuple(attempts), warnings=tuple(warnings)
        )

    # --- one call site: primary reply, then at most one repair ---

    def _with_repair(
        self,
        adapter: LLMAdapter,
        messages: list[Message],
        budget: CallBudget,
        attempts: list[Attempt],
        kind: str,
        *,
        repair: bool = True,
    ) -> IRPatch | None:
        raw = self._call(adapter, messages, budget, attempts, kind)
        patch, error = self._validate(raw.text)
        self._mark(attempts, patch is not None, error)
        if patch is not None or not repair:
            return patch
        fix = [
            *messages,
            Message("assistant", raw.text[:4000]),
            Message(
                "user",
                f"That reply was not valid: {error}\nReturn only the corrected JSON object.",
            ),
        ]
        raw = self._call(adapter, fix, budget, attempts, "repair")
        patch, error = self._validate(raw.text)
        self._mark(attempts, patch is not None, error)
        return patch

    @staticmethod
    def _validate(text: str) -> tuple[IRPatch | None, str]:
        try:
            return IRPatch.model_validate(parse_patch_json(text)), ""
        except json.JSONDecodeError as exc:
            return None, f"not valid JSON ({exc.msg} at position {exc.pos})"
        except ValueError as exc:
            if isinstance(exc, ValidationError):
                first = exc.errors()[:3]
                return None, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in first)
            return None, str(exc)

    @staticmethod
    def _mark(attempts: list[Attempt], ok: bool, error: str) -> None:
        last = attempts[-1]
        attempts[-1] = Attempt(
            last.kind,
            ok,
            last.mode,
            last.latency_s,
            last.prompt_tokens,
            last.completion_tokens,
            error,
        )

    # --- one model call: retry on transient errors, step down the output mode ---

    def _call(
        self,
        adapter: LLMAdapter,
        messages: list[Message],
        budget: CallBudget,
        attempts: list[Attempt],
        kind: str,
    ) -> RawResult:
        mode = self._start_mode(adapter)
        retried = False
        while True:
            budget.take()
            try:
                raw = adapter.generate(
                    messages,
                    json_schema=self._schema if mode == "schema" else None,
                    mode=mode,
                    params=self._params,
                )
            except LLMUnsupportedModeError as exc:
                attempts.append(Attempt("step_down", False, mode, error=str(exc)))
                lower = weaker(mode)
                if lower is None:
                    raise
                mode = lower
                continue
            except TRANSIENT as exc:
                attempts.append(Attempt("retry" if retried else kind, False, mode, error=str(exc)))
                if retried:
                    raise
                retried = True
                self._sleep(1.0)
                continue
            attempts.append(
                Attempt(
                    "retry" if retried else kind,
                    True,
                    mode,
                    raw.latency_s,
                    raw.usage.prompt_tokens,
                    raw.usage.completion_tokens,
                )
            )
            return raw
