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
from campaign_agent.api.users import USER_HEADER, User, UserTokenError, UserVerifier

log = logging.getLogger("campaign_agent.api")


def create_app(
    gateway: Gateway,
    *,
    token: str,
    users: UserVerifier | None = None,
    require_user: bool = False,
) -> FastAPI:
    """`users`: checks the signed-in person's own token (api/users.py). `require_user`: refuse requests without one."""
    app = FastAPI(title="Campaign Agent gateway", docs_url=None, redoc_url=None, openapi_url=None)

    def guard(request: Request) -> None:
        if not token_ok(bearer(request.headers.get("authorization")), token):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    warned: list[bool] = []

    def user_of(request: Request) -> User | None:
        """The verified person, or None when no user token was sent and none is required."""
        raw = (request.headers.get(USER_HEADER) or "").strip()
        if not raw:
            if require_user:
                raise HTTPException(status_code=401, detail="missing user token")
            return None
        if users is None:
            # Not set up yet (no API_SUPABASE_URL): the dashboard may already forward its users' tokens, so the token is
            # ignored, as before user tokens existed, and the request goes on with `requested_by`. Never a refusal: that
            # stopped every dashboard request on 2026-10-17. With API_REQUIRE_USER the gateway refuses to start instead.
            if not warned:
                log.warning("a user token arrived but API_SUPABASE_URL is not set: it is ignored")
                warned.append(True)
            return None
        try:
            return users.verify(raw)
        except UserTokenError as exc:
            log.warning("user token refused: %s", exc)
            raise HTTPException(status_code=401, detail="invalid user token") from exc

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
        return JSONResponse(
            {"detail": "the database is not set up for this (a migration is missing)"},
            status_code=503,
        )

    app.include_router(routes_health.router)
    app.include_router(routes_campaigns.make_router(gateway, guard, user_of))
    app.include_router(routes_jobs.make_router(gateway, guard, user_of))
    return app
