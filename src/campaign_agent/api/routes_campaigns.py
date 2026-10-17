from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from campaign_agent.api.schemas import RequestIn
from campaign_agent.api.service import Gateway
from campaign_agent.api.users import User


def make_router(gateway: Gateway, guard: Any, user_of: Any = lambda: None) -> APIRouter:
    router = APIRouter(prefix="/v1/campaigns", dependencies=[Depends(guard)])

    @router.post("/{campaign_id}/requests", status_code=202)
    def send(
        campaign_id: UUID,
        body: RequestIn,
        user: User | None = Depends(user_of),  # noqa: B008
    ) -> dict[str, Any]:
        """Send the Campaign Agent a request about a campaign: `interpret` (start), `answer`, `edit`, `suggestion`,
        `approve`, `discard`, `rollback` or `deactivate`. It is only recorded here; the Campaign Agent does the work, so
        this returns at once (202) with the request id to follow. With a verified user token the approval record is
        that person's email, whatever `requested_by` says, and only an admin may send a request."""
        if user is not None and not user.admin:
            raise HTTPException(status_code=403, detail="only an admin can change a campaign")
        return gateway.send_request(
            str(campaign_id),
            body.request_type,
            body.payload,
            body.base_revision,
            user.email if user is not None else body.requested_by,
            body.idempotency_key,
        )

    @router.get("")
    def index(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:  # noqa: B008
        """The newest campaigns that are not deleted, with their `workflow_status` (api/workflow.py)."""
        return {"campaigns": gateway.campaigns(limit)}

    @router.get("/{campaign_id}")
    def show(campaign_id: UUID) -> dict[str, Any]:
        """What to put on the screen: the state, the `workflow_status` across both agents (and `error` when a person
        must look), the revision, `review_view` (the proposal), the newest request, and `execution` (counts of the
        Marketing Agent's leads and emails for this campaign; null until it reports any)."""
        return gateway.campaign(str(campaign_id))

    return router
