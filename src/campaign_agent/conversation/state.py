"""What the conversation remembers between turns: the review state and the questions asked."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from campaign_agent.conversation.state_machine import Event, State, next_state
from campaign_agent.questions.models import Question

MAX_OPEN = 3  # at most this many unanswered questions at a time


class AskedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    question_id: str
    status: Literal["open", "answered", "skipped"]
    turn: int


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: State = State.DRAFT
    has_published: bool = False
    turn: int = 0
    asked: dict[str, AskedQuestion] = {}  # key -> record; a key is never asked twice
    open: tuple[Question, ...] = ()

    def after(self, event: Event) -> "ConversationState":
        new = next_state(self.state, event, has_published=self.has_published)
        return self.model_copy(
            update={"state": new, "has_published": self.has_published or new is State.PUBLISHED}
        )
