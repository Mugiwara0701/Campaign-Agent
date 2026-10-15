"""python -m campaign_agent autopilot: type a campaign into the open window, answer the manager, ask for a change,
approve it, and report each step, so nobody has to press anything.

The window does the work (the same buttons a person would press, visible on screen). This command only leaves it a plan
and prints what it reports. Run it on the office machine that shows the window (`python -m campaign_agent ui`); from the
laptop: `bash deploy/remote.sh agent autopilot ...`.
"""

import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

HOST = "127.0.0.1"
# Yesterday's test campaign, and a change that shows the country hint (two more countries).
PRESET_MESSAGE = (
    "Find automotive and industrial automation companies in Germany that need embedded Linux and Yocto "
    "engineering help."
)
PRESET_EDIT = "Also include Austria and Switzerland."
WINDOW_WAIT_S = 30  # no step reported by then: no window is open


def _headers(port: int) -> dict[str, str]:
    return {"X-CA-Window": "1", "Origin": f"http://{HOST}:{port}"}


def run_autopilot(
    *,
    message: str | None,
    edit: str | None,
    approve: bool,
    answers: list[str] | None,
    start_ma: bool = False,
    timeout_s: int,
    port: int = 8765,
    out: Callable[[str], None] = lambda text: print(text, flush=True),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    client: httpx.Client | None = None,
) -> int:
    """0 = the plan finished, 1 = a step failed, 2 = it could not start or nothing happened."""
    http = client or httpx.Client(base_url=f"http://{HOST}:{port}", timeout=15.0)
    body: dict[str, Any] = {
        "message": message or PRESET_MESSAGE,
        "edit": PRESET_EDIT if edit is None and message is None else (edit or None),
        "approve": approve,
        "start": start_ma,
        "answers": answers if answers else "first",
        "timeout": timeout_s,
    }
    try:
        r = http.post("/api/autopilot", json=body, headers=_headers(port))
    except httpx.HTTPError:
        out(
            f"The window is not running on this machine (nothing answers on {HOST}:{port}). Start it: python -m campaign_agent ui"
        )
        return 2
    if r.status_code != 200:
        out(f"Could not start: {r.json().get('detail', r.status_code)}")
        return 2
    out(
        f"Plan {r.json()['id']}: campaign={body['message'][:70]!r} edit={body['edit']!r} approve={approve} start_ma={start_ma}"
    )
    shown = 0
    started = clock()
    while clock() - started < timeout_s + 60:
        sleep(2)
        try:
            status = http.get("/api/autopilot").json()
        except httpx.HTTPError:
            out("The window stopped answering.")
            return 2
        for entry in status["log"][shown:]:
            out(f"  {entry['at']}  {entry['line']}")
        shown = len(status["log"])
        if status["state"] == "done":
            return 0
        if status["state"] == "failed":
            return 1
        if not status["log"] and clock() - started > WINDOW_WAIT_S:
            out(
                "No window picked the plan up. Is the window open? Start it: python -m campaign_agent ui"
            )
            return 2
    out("Gave up waiting.")
    return 2


def main(args: Any) -> int:
    return run_autopilot(
        message=args.message_flag or args.message,
        edit=args.edit,
        approve=not args.no_approve,
        answers=[a for a in (args.answer or []) if a],
        start_ma=args.start_ma,
        timeout_s=args.timeout,
        port=args.ui_port,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(2)
