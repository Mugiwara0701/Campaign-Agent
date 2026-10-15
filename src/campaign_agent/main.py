"""The dashboard gateway: a small web service the dashboard's server calls over HTTPS (docs/dashboard-api.md).

It sits in front of the same database the dashboard could write to itself. It does not talk to the office machine, which
stays private and polls the database as before. Run it with `python -m campaign_agent api` (Render does this).
"""

import logging

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from campaign_agent.api import routes_campaigns, routes_health, routes_jobs
from campaign_agent.api.auth import bearer, token_ok
from campaign_agent.api.service import Gateway, NotFound, Unavailable

log = logging.getLogger("campaign_agent.api")


def create_app(gateway: Gateway, *, token: str) -> FastAPI:
    app = FastAPI(title="Campaign Agent gateway", docs_url=None, redoc_url=None, openapi_url=None)

    def guard(request: Request) -> None:
        if not token_ok(bearer(request.headers.get("authorization")), token):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, _exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(Unavailable)
    async def _unavailable(_request: Request, _exc: Unavailable) -> JSONResponse:
        log.warning("database unavailable")
        return JSONResponse({"detail": "the database is not reachable; try again"}, status_code=503)

    @app.exception_handler(psycopg.errors.UndefinedFunction)
    @app.exception_handler(psycopg.errors.UndefinedTable)
    @app.exception_handler(psycopg.errors.InsufficientPrivilege)
    async def _not_set_up(_request: Request, exc: Exception) -> JSONResponse:
        log.warning("the database is not set up for this call: %s", type(exc).__name__)
        return JSONResponse({"detail": "the database is not set up for this (a migration is missing)"}, status_code=503)

    app.include_router(routes_health.router)
    app.include_router(routes_campaigns.make_router(gateway, guard))
    app.include_router(routes_jobs.make_router(gateway, guard))
    return app

