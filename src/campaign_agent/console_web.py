"""The Campaign Agent window: python -m campaign_agent ui opens it in Chromium.

A local page, read-only: a pixel-art office where the Campaign Agent is the manager and the Marketing Agent the
employee (console_page.py). It shows what collect() finds and refreshes itself. "Demo" plays a scripted story in the
browser and labels it SIMULATED.
It listens on 127.0.0.1 only and refuses any other Host header, so a web page on the internet cannot read it.
"""

import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any

import httpx
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from campaign_agent import console_flow
from campaign_agent.console import collect
from campaign_agent.console_demo import STORY
from campaign_agent.console_page import PAGE
from campaign_agent.settings import Settings
from campaign_agent.store.db import Database

HOST = "127.0.0.1"
CACHE_S = 3.0
CHROMIUM_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def create_app(db: Database, settings: Settings, port: int) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    allowed = {f"{HOST}:{port}", f"localhost:{port}"}
    lock = threading.Lock()
    cache: dict[str, Any] = {"snap": None, "at": 0.0}

    @app.middleware("http")
    async def only_this_machine(request: Request, call_next: Any) -> Any:
        if request.headers.get("host", "") not in allowed:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "POST" and (
            request.headers.get("x-ca-window") != "1"
            or request.headers.get("origin", f"http://{request.headers.get('host')}")
            not in {f"http://{h}" for h in allowed}
        ):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        with lock:
            # One check at a time, and a result less than 3 s old is reused: the checks take seconds on a slow link.
            if cache["snap"] is None or time.monotonic() - cache["at"] > CACHE_S:
                # A fresh connection per check: one kept open for hours can go stale and then block the window.
                url = getattr(settings, "database_url", None)
                fresh = Database(url.get_secret_value()) if url else db
                try:
                    cache["snap"] = collect(fresh, settings)
                finally:
                    if fresh is not db:
                        fresh.close()
                cache["at"] = time.monotonic()
            snap = cache["snap"]
        return {
            "lines": [asdict(line) for line in snap.lines],
            "requests": snap.requests,
            "events": snap.events,
        }

    @app.get("/api/demo")
    def demo() -> list[list[Any]]:
        return [list(step) for step in STORY]

    # --- the CEO's part: these write requests for the Campaign Agent worker, exactly as the dashboard does -----------
    def own_db(admin: bool) -> Database:
        # The CEO box stands in for the dashboard, which owns the campaign rows: like `try`, it uses the dev connection
        # (MIGRATE_DATABASE_URL, else DATABASE_URL). Everything the Campaign Agent does for itself uses its own role.
        url = (getattr(settings, "migrate_database_url", None) if admin else None) or getattr(
            settings, "database_url", None
        )
        return Database(url.get_secret_value()) if url else db

    def guarded(fn: Any, *args: Any, admin: bool = False) -> Any:
        fresh = own_db(admin)
        try:
            return fn(fresh, *args)
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if fresh is not db:
                fresh.close()

    @app.post("/api/campaigns")
    def new_campaign(body: dict[str, Any] = Body(...)) -> dict[str, str]:  # noqa: B008
        return {
            "campaign_id": guarded(console_flow.start, str(body.get("message", "")), admin=True)
        }

    @app.get("/api/campaigns/latest")
    def campaign_latest() -> dict[str, Any]:
        return {"campaign": guarded(console_flow.latest, admin=True)}

    @app.get("/api/campaigns/{campaign_id}")
    def campaign_status(campaign_id: str) -> dict[str, Any]:
        return guarded(console_flow.status, campaign_id)  # type: ignore[no-any-return]

    @app.post("/api/campaigns/{campaign_id}/answer")
    def campaign_answer(campaign_id: str, body: dict[str, Any] = Body(...)) -> dict[str, bool]:  # noqa: B008
        raw = {str(k): str(v) for k, v in dict(body.get("answers") or {}).items()}
        guarded(console_flow.answer, campaign_id, raw, admin=True)
        return {"ok": True}

    @app.post("/api/campaigns/{campaign_id}/edit")
    def campaign_edit(campaign_id: str, body: dict[str, Any] = Body(...)) -> dict[str, bool]:  # noqa: B008
        guarded(console_flow.edit, campaign_id, str(body.get("text", "")), admin=True)
        return {"ok": True}

    # --- autopilot: `python -m campaign_agent autopilot` leaves a plan here; the open window carries it out, step by
    # step, with the same buttons a person would press, and reports each step back --------------------------------
    autopilot: dict[str, Any] = {"plan": None, "log": [], "state": "idle"}
    PLAN_KEYS = ("message", "edit", "approve", "answers", "timeout", "start")

    @app.post("/api/autopilot")
    def autopilot_start(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        message = " ".join(str(body.get("message", "")).split())[:2000]
        if not message:
            raise HTTPException(status_code=400, detail="a plan needs a campaign message")
        if autopilot["state"] in ("waiting", "running"):
            raise HTTPException(status_code=409, detail="an autopilot run is already going")
        answers = body.get("answers", "first")
        plan = {
            "id": uuid.uuid4().hex[:8],
            "message": message,
            "edit": " ".join(str(body.get("edit") or "").split())[:2000] or None,
            "approve": bool(body.get("approve", True)),
            "start": bool(body.get("start", False)),
            "answers": [str(a)[:100] for a in answers][:10]
            if isinstance(answers, list)
            else "first",
            "timeout": max(60, min(int(body.get("timeout", 3600)), 7200)),
        }
        assert set(plan) >= set(PLAN_KEYS)  # noqa: S101
        autopilot.update(plan=plan, log=[], state="waiting")
        return {"id": plan["id"]}

    @app.get("/api/autopilot")
    def autopilot_status() -> dict[str, Any]:
        # a plan is handed to the window once ("waiting"); after that the window reports progress
        plan = autopilot["plan"] if autopilot["state"] == "waiting" else None
        return {
            "plan": plan,
            "id": (autopilot["plan"] or {}).get("id"),
            "state": autopilot["state"],
            "log": autopilot["log"],
        }

    @app.post("/api/autopilot/progress")
    def autopilot_progress(body: dict[str, Any] = Body(...)) -> dict[str, bool]:  # noqa: B008
        if not autopilot["plan"] or body.get("id") != autopilot["plan"]["id"]:
            raise HTTPException(status_code=404, detail="no such run")
        state = str(body.get("state", "running"))
        autopilot["log"].append(
            {
                "at": time.strftime("%H:%M:%S"),
                "line": str(body.get("line", ""))[:300],
                "state": state,
            }
        )
        autopilot["state"] = state if state in ("done", "failed") else "running"
        return {"ok": True}

    @app.post("/api/campaigns/{campaign_id}/approve")
    def campaign_approve(
        campaign_id: str,
        body: dict[str, Any] = Body(default={}),  # noqa: B008
    ) -> dict[str, bool]:
        guarded(
            console_flow.approve, campaign_id, bool(body.get("start_pipeline", False)), admin=True
        )
        return {"ok": True}

    @app.get("/api/ca-control")
    def ca_control_state() -> dict[str, Any]:
        return {"control": guarded(console_flow.control_state)}

    @app.post("/api/ca-control")
    def ca_control_request(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        return guarded(console_flow.control_request, str(body.get("state", "")), admin=True)  # type: ignore[no-any-return]

    @app.get("/api/pipeline")
    def pipeline_state() -> dict[str, Any]:
        return {"pipeline": guarded(console_flow.pipeline_state)}

    @app.post("/api/pipeline")
    def pipeline_request(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        return guarded(console_flow.pipeline, str(body.get("state", "")))  # type: ignore[no-any-return]

    @app.get("/{rest:path}")
    def missing(rest: str) -> None:
        raise HTTPException(status_code=404)

    return app


def find_chromium(which: Any = shutil.which) -> str | None:
    return next((path for name in CHROMIUM_NAMES if (path := which(name))), None)


def server_is_up(port: int) -> bool:
    """Is a window server already answering on this port? (Then another one must not be started.)"""
    try:
        return httpx.get(f"http://{HOST}:{port}/", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def window_is_open(url: str, run: Any = subprocess.run) -> bool:
    """Is a Chromium app window for this address already open? (Chromium opens one more window per launch.)"""
    return bool(
        run(["pgrep", "-f", "--", f"--app={url}"], capture_output=True, check=False).returncode == 0
    )


HANDOFF_S = 5.0


def window_closed(
    window: "subprocess.Popen[bytes] | None", started: float, now: float | None = None
) -> bool:
    """The person closed the window, so the app quits. A Chromium that exits within a few seconds only handed the
    page to a window that is already open: then the server keeps running for that window."""
    if window is None or window.poll() is None:
        return False
    return (now if now is not None else time.monotonic()) - started > HANDOFF_S


def window_command(browser: str, url: str, *, fullscreen: bool = True) -> list[str]:
    """How the window is opened: an app window without tabs or address bar, and by default full screen (no title bar, no
    desktop panel), like a fullscreen video. F11 leaves and re-enters full screen; Alt+F4 closes it, which quits the app."""
    return [
        browser,
        f"--app={url}",
        "--user-data-dir=/tmp/campaign-agent-ui",
        "--no-first-run",
        *(["--start-fullscreen"] if fullscreen else []),
    ]


def run_ui(
    db: Database | None,
    settings: Settings,
    *,
    port: int = 8765,
    open_window: bool = True,
    fullscreen: bool = True,
) -> int:
    import sys

    if db is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if server_is_up(port):
        url = f"http://{HOST}:{port}/"
        print(f"The Campaign Agent window is already running: {url}")
        if open_window and not window_is_open(url) and (browser := find_chromium()):
            subprocess.Popen(  # noqa: S603
                window_command(browser, url, fullscreen=fullscreen),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        db.close()
        return 0
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(db, settings, port),
            host=HOST,
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(0.1)
    if not server.started:
        print(
            f"could not listen on {HOST}:{port} (is another window already open?)", file=sys.stderr
        )
        return 1
    url = f"http://{HOST}:{port}/"
    browser = find_chromium() if open_window and not window_is_open(url) else None
    print(
        f"Campaign Agent window: {url}"
        + ("" if browser or not open_window else "  (Chromium not found: open it yourself)")
    )
    window: subprocess.Popen[bytes] | None = None
    started = time.monotonic()
    try:
        if browser:
            window = subprocess.Popen(  # noqa: S603
                window_command(browser, url, fullscreen=fullscreen),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        while thread.is_alive() and not window_closed(window, started):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        db.close()
    return 0
