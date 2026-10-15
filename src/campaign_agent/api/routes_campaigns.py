from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from campaign_agent.api.schemas import RequestIn
from campaign_agent.api.service import Gateway


def make_router(gateway: Gateway, guard: Any) -> APIRouter:
    router = APIRouter(prefix="/v1/campaigns", dependencies=[Depends(guard)])

    @router.post("/{campaign_id}/requests", status_code=202)
    def send(campaign_id: UUID, body: RequestIn) -> dict[str, Any]:
        """Send the Campaign Agent a request about a campaign: `interpret` (start), `answer`, `edit`, `suggestion`,
        `approve`, `discard`, `rollback` or `deactivate`. It is only recorded here; the Campaign Agent does the work, so
        this returns at once (202) with the request id to follow."""
        return gateway.send_request(
            str(campaign_id),
            body.request_type,
            body.payload,
            body.base_revision,
            body.requested_by,
            body.idempotency_key,
        )

    @router.get("/{campaign_id}")
    def show(campaign_id: UUID) -> dict[str, Any]:
        """What to put on the screen: the state, the revision, `review_view`, and the newest request."""
        return gateway.campaign(str(campaign_id))

    return router
