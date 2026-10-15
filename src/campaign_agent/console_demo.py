"""A scripted story of one campaign going through the Campaign Agent: python -m campaign_agent console --demo.

It is a SIMULATION for showing the flow. It talks to no database, model, Marketing Agent or network, and every line
carries the SIMULATED tag, so it cannot be mistaken for the live view.
"""

import time
from collections.abc import Callable

_TAG = "\033[35m[SIMULATED]\033[0m"
_DIM, _OFF = "\033[90m", "\033[0m"

# (seconds to wait, who, text)
STORY: tuple[tuple[float, str, str], ...] = (
    (0.6, "dashboard", 'request from priya@example: "Find SaaS CFOs in Germany, 50-200 staff, skip agencies"'),
    (0.8, "guard", "input checked: no secrets, no instructions aimed at the agent"),
    (1.0, "model", "reading the request (qwen3.5:9b, schema mode)"),
    (1.4, "extract", "industry=SaaS, role=CFO, country=DE, size=50-200, exclude=agencies"),
    (0.8, "gap", "1 thing is unclear: does \"Germany\" include Austria and Switzerland?"),
    (0.8, "question", "asked the dashboard: DACH or Germany only?"),
    (1.8, "dashboard", "answer: Germany only"),
    (0.8, "compile", "built the overlay: search.queries, filters.country, exclude_industries"),
    (0.9, "validate", "levels 1-5 passed: schema, keys, ranges, secrets, merge with the base config"),
    (0.9, "validate", "level 6 skipped: no Marketing Agent checkout on this machine"),
    (1.2, "dashboard", "approved by priya@example (version 3)"),
    (0.8, "publish", "version 3 is live, sha256 9f2c41ab7d03e1c5"),
    (1.0, "link", "Marketing Agent asked for the live campaign over Tailscale"),
    (0.8, "link", "sent version 3 (ETag c81e.3.9f2c41ab7d03e1c5)"),
    (1.2, "ma", "event applied: version 3 is what the next pass will use"),
    (1.8, "ma", "event blocker MA_CHROME_DOWN: the browser did not start (dedupe chrome)"),
    (0.8, "notify", "shown on the dashboard and sent to Slack: needs a person"),
    (2.0, "ma", "event resolved MA_CHROME_DOWN: the browser is back"),
    (0.8, "state", "all clear; waiting for the next request"),
)


def run_demo(
    *,
    out: Callable[[str], None] = lambda text: print(text, flush=True),
    sleep: Callable[[float], None] = time.sleep,
    speed: float = 1.0,
) -> int:
    out(f"\033[1mCampaign Agent\033[0m  {_TAG}  This is a scripted demo. Nothing below is real.\n")
    try:
        for wait, who, text in STORY:
            sleep(wait / speed)
            out(f"{_DIM}{time.strftime('%H:%M:%S')}{_OFF}  {who:<10} {text}")
        out(f"\n{_TAG} end of demo. For the real state run: python -m campaign_agent console")
    except KeyboardInterrupt:
        out("")
    return 0
