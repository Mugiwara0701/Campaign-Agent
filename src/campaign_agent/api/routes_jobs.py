from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from campaign_agent.api.schemas import StateIn
from campaign_agent.api.service import Gateway
from campaign_agent.api.users import User


def make_router(gateway: Gateway, guard: Any, user_of: Any = lambda: None) -> APIRouter:
    router = APIRouter(prefix="/v1", dependencies=[Depends(guard)])

    @router.get("/requests/{request_id}")
    def request_status(request_id: UUID) -> dict[str, Any]:
        """One request: `status` (pending, processing, done, failed), `error_code`, `error_detail`, `result`."""
        return gateway.request_status(str(request_id))

    @router.get("/ca")
    def ca_status() -> dict[str, Any]:
        """Is the Campaign Agent on the office machine alive (`online`), and is it paused (`control`)."""
        return gateway.ca_status()

    @router.post("/ca/state")
    def ca_state(body: StateIn, user: User | None = Depends(user_of)) -> dict[str, Any]:  # noqa: B008
        """Pause or resume the Campaign Agent. Pausing also stops the Marketing Agent; resuming starts it again if it was
        running. With a verified user token: anyone signed in may pause (the safety brake), only an admin may resume."""
        if user is not None and body.state == "running" and not user.admin:
            raise HTTPException(status_code=403, detail="only an admin can resume the agent")
        return gateway.set_ca_state(
            body.state, user.email if user is not None else body.requested_by
        )

    return router
