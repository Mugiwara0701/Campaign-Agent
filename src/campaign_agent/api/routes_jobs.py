from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from campaign_agent.api.schemas import StateIn
from campaign_agent.api.service import Gateway


def make_router(gateway: Gateway, guard: Any) -> APIRouter:
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
    def ca_state(body: StateIn) -> dict[str, Any]:
        """Pause or resume the Campaign Agent. Pausing also stops the Marketing Agent; resuming starts it again if it was running."""
        return gateway.set_ca_state(body.state, body.requested_by)

    return router
