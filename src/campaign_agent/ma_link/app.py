"""The three routes the Marketing Agent calls (docs/ca-ma-link.md, section 5). Nothing else is served."""

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from campaign_agent.ma_link.auth import token_ok
from campaign_agent.ma_link.events import MAX_BODY_BYTES, parse_batch
from campaign_agent.ma_link.service import (
    NO_CAMPAIGN_TAG,
    LinkBackend,
    LiveCampaign,
    rejected_payload,
)

log = logging.getLogger("campaign_agent.ma_link")

API_VERSION = "1"
MAX_WAIT_S = 30
POLL_S = 2.0


def _tag_of(live: LiveCampaign | None) -> str:
    return live.tag if live is not None else NO_CAMPAIGN_TAG


def _quote(tag: str) -> str:
    return f'"{tag}"'


def _unquote(header: str | None) -> str | None:
    if not header:
        return None
    return header.strip().removeprefix("W/").strip('"')


def create_app(
    backend: LinkBackend,
    *,
    token: str,
    token_previous: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    # No docs, no OpenAPI: the only clients are the Marketing Agent and the people who read docs/ca-ma-link.md.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[..., Any]) -> Response:
        if not token_ok(request.headers.get("authorization"), token, token_previous):
            client = request.client.host if request.client else "unknown"
            log.warning("link: wrong or missing token", extra={"client": client})
            return JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/ma/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "time": datetime.now(UTC).isoformat(), "api": API_VERSION}

    @app.get("/ma/v1/campaign/active")
    def active(request: Request, wait: int = 0) -> Response:
        known = _unquote(request.headers.get("if-none-match"))
        deadline = clock() + max(0, min(wait, MAX_WAIT_S))
        live = backend.live_campaign()
        # With a known tag, hold the request until what is live differs from it, or the time is up.
        while known is not None and _tag_of(live) == known and clock() < deadline:
            sleep(min(POLL_S, max(0.0, deadline - clock())))
            live = backend.live_campaign()
        tag = _tag_of(live)
        headers = {"ETag": _quote(tag)}
        if known is not None and tag == known:
            return Response(status_code=304, headers=headers)
        if live is None:
            return Response(status_code=204, headers=headers)
        return JSONResponse(live.payload(), headers=headers)

    @app.post("/ma/v1/events")
    async def events(request: Request) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse({"error": "body_too_big"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"error": "body_too_big"}, status_code=413)
        try:
            parsed = parse_batch(json.loads(body))
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if parsed is None:
            return JSONResponse({"error": "invalid_batch"}, status_code=422)
        good, bad = parsed
        accepted = await run_in_threadpool(backend.store_events, good) if good else []
        return JSONResponse({"accepted": accepted, "rejected": rejected_payload(bad)})

    return app
