"""Question, choice and answer types. Pydantic, so a conversation can be stored and sent as JSON."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from campaign_agent.ir.patch import PatchOp, UnsupportedClaim

PLACEHOLDER = "$answer"  # replaced by the user's free-text answer inside an effect's ops


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Effect(_M):
    """What choosing an option does. All of it goes through the merger as the user's own change."""

    ops: tuple[PatchOp, ...] = ()
    reextract: str | None = None  # run extraction on this text as an edit request
    unsupported: UnsupportedClaim | None = None


class Choice(_M):
    id: str
    label: str
    effect: Effect = Effect()


QuestionKind = Literal["conflict", "drift", "unclear", "gap", "ambiguity", "preference"]


class Question(_M):
    id: str
    key: str  # path|reason|instance: a question with this key is never asked twice
    kind: QuestionKind
    text: str
    choices: tuple[Choice, ...] = ()
    multi: bool = False  # more than one choice may be picked
    text_effect: Effect | None = (
        None  # set when a free-text answer is allowed; ops may hold PLACEHOLDER
    )
    skippable: bool = True
    priority: int = 0

    @property
    def allow_text(self) -> bool:
        return self.text_effect is not None


class Answer(_M):
    question_id: str
    value: Annotated[str, StringConstraints(max_length=500)] | list[str] | None = None
    skip: bool = False

    @property
    def is_skip(self) -> bool:
        return self.skip or (
            isinstance(self.value, str) and self.value.strip().casefold() == "skip"
        )


class FollowUp(_M):
    """Work the caller must do after an answer: read this text as an edit request."""

    reextract: str


class AnswerError(_M):
    code: Literal[
        "unknown_question",
        "already_answered",
        "not_skippable",
        "invalid_choice",
        "invalid_text",
        "invalid_value",
    ]
    message: str


Priority = Annotated[int, Field(ge=0, le=9)]
