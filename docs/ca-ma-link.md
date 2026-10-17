# The Campaign Agent to Marketing Agent Link (Tailscale)

How the Campaign Agent (CA) and the Marketing Agent (MA) talk to each other directly, without fixed IP addresses, and how
a blocker in the MA reaches you. Numbers (timeouts, sizes) are starting values, not measurements.

## 0. Status (2026-10-13)

Code for steps 2 to 6 of section 12 is written in both repositories (branches `feature/ma-link` here and `feature/ca-link` in
`Marketing-AI-Agent`), **not yet merged, not yet run on the office machines**. Nothing was run on the laptop; CI is the first run.

| Built | Where |
|---|---|
| CA listener: `/health`, `/campaign/active` (ETag, long poll), `/events`; token check; binds to the Tailscale address only | `src/campaign_agent/ma_link/`, started by `python -m campaign_agent start` when `MA_LINK_ENABLED=true` |
| Event table, dashboard grants, `ma_events` in the review view, pruning | `migrations/0017_ma_event.sql`, `store/repositories.py`, `view.py`, `jobs/manager.py` |
| MA client: pickup with cache, sha256 and shape check, merge, status row; event outbox and sender; pass-failed blocker | `agent/campaign_link.py` (MA repo), hooks in `agent/leadgen/service.py`, `agent/control.py`, `agent/supervisor.py` |
| Tests | `tests/unit/test_ma_link.py`, `tests/integration/test_ma_link_store.py` (CA); `agent/tests/test_campaign_link.py` (MA) |

| Not built yet | Notes |
|---|---|
| Supabase fallback for pickup | The MA's database role has no grant on `campaign_state` / `campaign_version`. With the link down it runs the cached campaign |
| The MA's strict, versioned loader (M8 item 1) | `check_overlay` in `agent/campaign_link.py` is a stand-in: known keys, right types, campaign-owned sections only. It never accepts more than the real loader should |
| `agent leads queries --campaign` (level 6 in the CA) | Still `skipped` in the CA until it exists |
| Early notice by long poll (step 8) | The route supports it; the MA does not use it yet |
| More blocker sources | Only `MA_CAMPAIGN_*`, `MA_PICKUP_FAILED`, `MA_LINK_AUTH` and `MA_PASS_FAILED` are emitted. Chrome, model, search, Slack and Gmail failures need hooks at their failure points |
| `429` rate limiting on the link | Not implemented; the listener is reachable only by the MA |

Read first: [publishing.md](publishing.md) (how a version becomes live), [marketing-agent-m8-work.md](marketing-agent-m8-work.md)
(what the MA must do to pick a campaign up) and [supabase-tables.md](supabase-tables.md).

## 1. What exists and what this adds

| Today | Status |
|---|---|
| Dashboard to CA: requests and state in Supabase (`campaign_request`, `campaign_state`) | Built, not changed by this document |
| CA to MA: the MA reads the live version from Supabase (`campaign_state.active_version_id`, `campaign_version`) | CA side built; the MA side (M8 items 5 and 6) is **not built** |
| MA to CA: the MA reports what it runs in `ma_campaign_status` (migration `0016`) | CA reads it; the MA writes it best-effort after each pickup (`agent/campaign_link.py`) |
| MA blockers (Chrome failed, model down, search blocked, ...) | **Nothing.** No way for the CA, the dashboard or you to hear about them |

This document adds **a private, direct link between the two office machines** over Tailscale. It carries two things:

1. **The campaign**, pushed to the MA quickly: the MA fetches the live version from the CA and is told as soon as a new
   one exists, instead of only reading the database at the start of a pass.
2. **Events from the MA**, including blockers, which the CA stores, shows in the dashboard view and (for the worst ones) the
   MA also posts to Slack.

The database stays the record of truth. The link is the fast path; the database is the fallback (section 8).

## 2. Principles

- **The MA stays a client.** It only makes outgoing connections, as today. Nothing listens on the MA machine for the CA.
  The CA is the only server in the link.
- **No fixed IP anywhere.** Machines are found by their Tailscale name (MagicDNS), which does not change when the router
  restarts or the LAN addresses change.
- **A campaign is data, never a command.** The link cannot make the MA run anything. It delivers a version that was approved
  by a person, with a sha256 the MA checks again. The only thing the MA sends back is events.
- **Email approval is untouched.** Every email is still approved by a person in Slack. The link has no field for it, and
  must never get one.
- **Nothing is lost when a side is down.** The MA keeps the last good campaign and queues events; the CA catches up.

## 3. Topology

```text
Dashboard server ──► Supabase ◄────────────── CA machine (campaign-agent service)
                    (campaign_request,                ▲  listens on its Tailscale address only
                     campaign_state, ...)             │  port 8787, bearer token
                                                      │  MA is the client
                                                MA machine (marketing-agent service)
                       both machines: tailscaled (system service), tagged tag:ca and tag:ma
```

Both machines also keep their outgoing connections to Supabase, which work with any IP address. Tailscale uses a direct
path over the LAN when it can and falls back to a relay when it cannot; the application does not need to know which.

## 4. Setting up Tailscale

The full step-by-step guide is [tailscale-setup.md](tailscale-setup.md); use that. The summary below is only for orientation.

Tailscale's free Personal plan is enough for two or three machines (check the current limits on tailscale.com/pricing).

### 4.1 One time, in the admin console (login.tailscale.com)

1. Create the tailnet with the account that will own it. Enable **MagicDNS**.
2. Create an **auth key** (reusable off, pre-approved, tagged `tag:ca`) for the CA machine and another (tagged `tag:ma`)
   for the MA machine. Tagged machines do not expire their node keys by default; confirm in the machine list that **key
   expiry is disabled** for both (otherwise they log out after about six months).
3. Replace the default "allow everything" policy with the policy below (Access controls tab).

```json
{
  "tagOwners": {
    "tag:ca": ["autogroup:admin"],
    "tag:ma": ["autogroup:admin"]
  },
  "acls": [
    { "action": "accept", "src": ["tag:ma"], "dst": ["tag:ca:8787"] }
  ]
}
```

This allows exactly one thing: the MA machine reaching port 8787 of the CA machine. The CA machine cannot open any
connection to the MA machine, and nothing else on the tailnet can reach either. If you also want to use SSH over
Tailscale from your laptop (as `deploy/remote.sh` does over the LAN), add your laptop as `tag:admin` and a rule
`tag:admin` to `tag:ca:22` and `tag:ma:22`. Do not add it unless you need it.

### 4.2 On each office machine (administrator account, once)

```bash
curl -fsSL https://tailscale.com/install.sh | sh        # installs and enables the tailscaled system service
sudo tailscale up --auth-key=tskey-... --hostname=campaign-agent --advertise-tags=tag:ca --ssh=false   # on the CA machine
sudo tailscale up --auth-key=tskey-... --hostname=marketing-agent --advertise-tags=tag:ma --ssh=false  # on the MA machine
sudo systemctl enable --now tailscaled
tailscale status                                         # both machines listed, "idle" or "active"
```

Use `--hostname=campaign-agent` and `--hostname=marketing-agent` so the names in this document are the real names. The
auth key is a secret: do not put it in git, in `.env` files or in documents. Remove it from your shell history.

On the **CA machine** also allow the port only on the Tailscale interface (the service account has no sudo, so the
administrator does this):

```bash
sudo ufw allow in on tailscale0 to any port 8787 proto tcp
```

`deploy/bootstrap-ubuntu.sh` (PART=system) should do the install and the firewall rule for the CA machine once this is
implemented; the auth key is always typed by a person.

### 4.3 Check it works

From the MA machine: `tailscale ping campaign-agent` answers, and later
`curl -s http://campaign-agent:8787/ma/v1/health` returns `{"status":"ok"}`.

### 4.4 The router restart test (do it once, on purpose)

With both machines connected and the service running, restart the router. The expectation, not yet measured: both
machines come back on the tailnet by themselves within a few minutes, with no change to any configuration, and the MA's
next call succeeds. Write the measured recovery time into [runbook.md](runbook.md). If it does not recover, the cause is
usually `tailscaled` not enabled at boot or the machine not getting its network back; see section 11.

## 5. The link API (served by the CA)

Base: `http://campaign-agent:8787/ma/v1`. Plain HTTP is acceptable because Tailscale encrypts and authenticates the
traffic end to end (WireGuard). If you prefer HTTPS, `tailscale serve` can terminate TLS with a certificate for the machine
name; that is an open decision (section 13).

The listener binds to the machine's Tailscale address (`tailscale ip -4`), never to `0.0.0.0`, so it is not reachable on
the LAN. If Tailscale is not up when the service starts, the listener retries every 15 s; the worker keeps running without it.

**Authentication.** `Authorization: Bearer <MA_LINK_TOKEN>`: a random value of at least 32 bytes, generated once,
stored in the `.env` of both machines (mode 600), compared in constant time. A wrong token gives `401` and is logged with the
source address, never with the token. Rotate by setting a new value on both machines; the CA accepts the current and the
previous token for one rollover (`MA_LINK_TOKEN_PREVIOUS`). This is a second layer on top of the Tailscale ACL.

All bodies are JSON, UTF-8, at most 64 KB. Times are UTC ISO 8601.

### 5.1 `GET /health`

No body. `200 {"status":"ok","time":"...","api":"1"}`. Needs the token as well, so it also checks the token works.

### 5.2 `GET /campaign/active`

The campaign the MA should run now. At most one is live.

- `200` with the live version:

  ```json
  {
    "campaign_id": "uuid",
    "version": 3,
    "sha256": "hex of overlay_yaml as stored",
    "contract_version": "1.0",
    "overlay_yaml": "config_version: ...",
    "approved_by": "someone",
    "approved_at": "2026-10-20T09:15:00Z"
  }
  ```

- `204` when nothing is live. The MA then behaves exactly as today (its base config).
- The response has an `ETag` that changes whenever what the MA should run changes: `"<campaign_id>.<version>.<first 16 of sha256>"`
  (a rollback to another version changes it even if the text is the same). `204` carries `ETag: "none"`. A request with
  `If-None-Match` of the current tag gets `304`.
- **Long poll:** `GET /campaign/active?wait=25` with `If-None-Match` holds the request for up to 25 s and returns as soon as
  the live version changes (including to "nothing live", which returns `204`), or `304` when the time runs out. This is how
  the MA learns of a new campaign within seconds, without the CA ever connecting to the MA.

The CA serves this from the same database rows the dashboard flow already writes (`campaign_state.active_version_id`,
`campaign_version`). It never serves a version that is not approved and live. The MA must check
`sha256(overlay_yaml) == sha256` itself.

### 5.3 `POST /events`

A batch of events from the MA:

```json
{
  "events": [
    {
      "event_id": "uuid made by the MA",
      "occurred_at": "2026-10-20T09:20:11Z",
      "kind": "blocker",
      "severity": "error",
      "code": "MA_CHROME_FAILED",
      "message": "Chrome did not start after 3 tries",
      "campaign_id": "uuid or null",
      "version": 3,
      "pass_id": "string or null",
      "dedupe_key": "chrome-failed",
      "details": {"attempts": 3}
    }
  ]
}
```

- `200 {"accepted": ["event_id", ...]}`. The CA stores events idempotently on `event_id`, so the MA can resend a batch
  after a timeout. Events it did not accept are listed in `"rejected": [{"event_id": "...", "reason": "..."}]` and must not
  be resent unchanged.
- At most 50 events per batch. `message` at most 300 characters, `details` at most 2 KB.
- **No secrets and no page contents in `details`.** Not tokens, passwords, email bodies, or fetched pages. The CA also
  refuses a batch that contains secret-like strings.
- `kind` is one of `applied`, `rejected`, `blocker`, `resolved`, `info`. `severity` is `info`, `warning`, `error` or
  `critical`.
- Events are not commands. Nothing the CA does depends on an event except showing it (section 7).

### 5.4 Errors

| Status | Meaning | The MA should |
|---|---|---|
| `401` | wrong or missing token | stop retrying quickly; log once; post to Slack that the link is misconfigured |
| `413`, `422` | body too big or invalid | not resend unchanged |
| `429` | too many calls (reserved, not sent today) | wait for `Retry-After` |
| `5xx`, timeout, connection refused | CA or network down | retry with backoff; use the fallback (section 8) |

## 6. Events: the codes

Codes are stable strings; the dashboard can show text for a known code and the raw message for an unknown one. The first
set (more can be added; never reuse a code for a different meaning):

| Code | Kind | Typical severity | Meaning |
|---|---|---|---|
| `MA_CAMPAIGN_APPLIED` | applied | info | A version was loaded and is running from this pass |
| `MA_CAMPAIGN_HASH_MISMATCH` | rejected | error | `sha256(overlay_yaml)` differs from the stored sha256; kept the last good campaign |
| `MA_CAMPAIGN_REJECTED` | rejected | error | The strict loader refused the overlay; the message is the loader's one-sentence reason |
| `MA_CONTRACT_UNSUPPORTED` | rejected | error | The MA does not load this `contract_version` |
| `MA_PICKUP_FAILED` | blocker | warning | Neither the link nor the database could be read; running the cached campaign |
| `MA_CHROME_FAILED` | blocker | error | The browser could not start or crashed repeatedly |
| `MA_LLM_DOWN` | blocker | error | Ollama does not answer |
| `MA_SEARCH_BLOCKED` | blocker | warning | The search engine blocks or asks for a captcha |
| `MA_SEARCH_QUOTA` | blocker | warning | The search quota or per-run page limit stopped discovery early |
| `MA_NO_LEADS` | blocker | warning | Several passes in a row found no new qualified lead (likely a too-narrow campaign) |
| `MA_DB_DOWN` | blocker | critical | The MA cannot reach Supabase |
| `MA_SLACK_FAILED` | blocker | error | Slack approval messages cannot be posted |
| `MA_GMAIL_AUTH` | blocker | error | Gmail login expired or was refused |
| `MA_PASS_FAILED` | blocker | error | A pass ended with an unexpected error (the message names the step) |
| `MA_LINK_AUTH` | blocker | error | The CA answered `401`. Not sent as an event (it would be refused too): the MA posts it to Slack |

A `blocker` stays **open** until the MA sends a `resolved` event with the same `dedupe_key` (or the same code and campaign
when there is none). A repeating failure must reuse its `dedupe_key`, so one open blocker is updated instead of a flood.
`critical` events are also posted to Slack by the MA itself, because the CA may be the thing that is down.

## 7. What the CA does with events

New migration (proposed `0017_ma_event.sql`, to be applied like the others with `deploy/remote.sh migrate`):

| Column | Content |
|---|---|
| `event_id uuid primary key` | the MA's id; makes storing idempotent |
| `received_at`, `occurred_at` | when the CA stored it; when it happened |
| `kind`, `severity`, `code`, `message` | as sent (checked against the allowed lists) |
| `campaign_id`, `version_no`, `pass_id` | optional context |
| `dedupe_key`, `details jsonb` | as sent |
| `resolved_at` | set when a `resolved` event with the same key arrives |

Grants: the `campaign_agent` role inserts and updates `resolved_at`; the `campaign_dashboard` role selects. Neither role
gets more. The MA never touches this table: it reaches it only through the CA.

In the dashboard view (`review_view`) the CA adds `ma_events`: the open blockers (newest first, at most 20) and the last
few applied or rejected events for the live campaign. The dashboard shows them next to `applied` and `ma_error`. The CA
does not act on a blocker by itself; **you decide** and answer in the normal conversation (for example, relax a threshold,
which creates a new version).

Retention: resolved events older than 30 days and `info` events older than 7 days are deleted, as described in
[retention.md](retention.md) (to be updated with this table).

## 8. What the MA does (the client)

Changes in the Marketing Agent repository. They extend M8 items 5 and 6 rather than replace them.

**Pickup.** At the start of each discovery pass, and once when the pipeline is started:

1. Call `GET /campaign/active` with `If-None-Match` of the cached version (5 s connect, 10 s read timeout).
2. If the link fails (any error, timeout, `5xx`), read the live version from Supabase instead (the M8 item 5 query).
3. If both fail, run the **cached last good campaign** (a file in the MA's `.cache/`, written after every successful
   load), or the base config if none exists, and emit `MA_PICKUP_FAILED`.
4. A version is used only if `sha256(overlay_yaml)` equals the sha256, and the strict loader accepts it. Otherwise: keep the
   last good one, emit `MA_CAMPAIGN_HASH_MISMATCH` or `MA_CAMPAIGN_REJECTED`, and write the same into the status row.
5. A good version is used **for the whole pass**. It is cached and reported with `MA_CAMPAIGN_APPLIED` and the status row.

**Early notice (optional, after the basics work).** While the pipeline is running, a background task long-polls
`GET /campaign/active?wait=25` with the version in use. When it changes, the MA notes "a new campaign is waiting" and
switches **only at the next pass boundary**, never in the middle of a pass. This only makes the dashboard's `applied` flag
turn `yes` sooner for a pipeline that is already running; it does not change when anything is applied.

**Sending events.** Events are first written to a local outbox (a small SQLite or JSON-lines file in the MA's `.cache/`,
bounded to 1000 events; when full, `info` events are dropped first, never open blockers). A background sender posts
batches of up to 50 every 5 s when there is something to send. On failure it backs off 1 s, 2 s, 4 s ... up to 60 s, with
random jitter, and keeps going. Events are removed from the outbox only after the CA lists them as accepted.

**Resolving the name every time.** The MA never keeps an IP address. It uses the host name on every connection, so a changed
Tailscale or LAN address is picked up on the next call. Do not cache DNS answers in the application.

**Critical events** are also posted to Slack by the MA using its existing Slack code, independently of the link.

**Secrets.** `MA_LINK_URL` (`http://campaign-agent:8787/ma/v1`) and `MA_LINK_TOKEN` are in the MA's `.env`, mode 600, and
never logged. The `.env` is not synced by `deploy/remote.sh sync` and is sent only with `push-secrets`.

## 9. What the CA does (the server)

Changes in this repository:

| Piece | Where | Notes |
|---|---|---|
| Settings: `ma_link_enabled` (default false), `ma_link_port` (8787), `ma_link_token`, `ma_link_token_previous` | `settings.py` | The listener is off unless enabled and a token of at least 32 characters is set |
| Link package: the three routes, token check, size limits, secret check | `src/campaign_agent/ma_link/` | FastAPI and uvicorn are already dependencies. The existing `api/` package holds empty placeholders; the link is a new, separate app so it can never expose dashboard routes |
| Start the listener beside the worker, on the Tailscale address, retry while it is down | `__main__.py` `_serve` | A failing listener must never stop the worker |
| Reads of the live version and the long-poll wake-up | `store/repositories.py` | Reuses the rows `ca_publish` writes; polls `campaign_state.active_version_id` every 2 s (the worker already polls at that rate) |
| Event storage and `ma_events` in the view | migration `0017`, `view.py`, `jobs/manager.py` | Section 7 |
| Deploy: install Tailscale, firewall rule, `.env` keys | `deploy/bootstrap-ubuntu.sh`, `deploy/remote.sh`, [runbook.md](runbook.md) | The auth key is typed by a person |
| Heartbeat shows the link state | `ca_heartbeat` | So the dashboard can tell "CA up, MA not calling" |

## 10. Security

- **Three layers:** the Tailscale ACL (only `tag:ma` to `tag:ca:8787`), the listener bound to the Tailscale address plus a
  firewall rule on `tailscale0`, and the bearer token. A mistake in one still leaves two.
- **The CA cannot call the MA.** The ACL has no rule for it and the CA has no client code for it.
- **No new write path into lead or email tables.** The link carries campaigns out and events in; events are stored in their
  own table and only displayed.
- **Untrusted input.** Everything the MA sends is validated (size, enums, UUIDs, secret-like strings) and shown to people
  as text, never as markup. Event text is never put in a model prompt.
- **Approval is unchanged.** `ca_publish` still decides what is live. The link serves only that.
- **If the CA machine is compromised,** the attacker could serve a bad campaign, but the MA still checks sha256 and the strict
  loader, caps every limit, and cannot be told to skip Slack approval (not a campaign field). If the MA machine is
  compromised, it could send false events, which only appear as text in the dashboard.
- Rotate the token if a machine is lost or reinstalled; remove a lost machine from the Tailscale admin console.

## 11. Failures and what happens

| Situation | Effect | Recovery |
|---|---|---|
| Router restarts | Link down for a while; MA runs the cached campaign and queues events | Tailscale reconnects by itself; the outbox drains. If not: `tailscale status` on both machines, `sudo systemctl restart tailscaled` |
| CA machine off or service stopped | Same; the MA falls back to Supabase for the live version | Start the service (`deploy/remote.sh up`); events arrive from the outbox |
| MA machine off | The dashboard shows the MA as offline; a campaign published meanwhile is applied when it returns | Nothing to do |
| Supabase unreachable | The dashboard and CA cannot work; the MA keeps running its cached campaign | Wait; `MA_DB_DOWN` is posted to Slack |
| Wrong or rotated token | The MA gets `401`, uses the Supabase fallback, posts a Slack warning | Fix `MA_LINK_TOKEN` on one side, then `up` |
| Tailscale logged out (key expired, machine removed) | Link down permanently until fixed | `sudo tailscale up --auth-key=...` again; check key expiry is disabled |
| Tailscale service itself unavailable | Existing connections usually keep working; new ones may not | The Supabase fallback keeps campaigns flowing; consider Headscale only if this becomes a real problem |
| Outbox full | Oldest `info` events dropped | Nothing; open blockers are kept |

## 12. Implementation plan

Do it in this order. Each step is useful alone and has a proof. Nothing runs on the laptop: tests run in GitHub CI, and
real checks run on the office machines through `deploy/remote.sh`.

| Step | Where | Done when |
|---|---|---|
| 1. Tailscale on both machines, ACL, names | the two machines, admin console | `tailscale ping` works both ways; the ACL test (MA reaches port 8787, CA cannot reach the MA, nothing else reaches either) is shown in the console's access preview |
| 2. CA listener with `/health`, token, bind address | CA repo | CI tests: wrong token `401`, right token `200`, body too big `413`, binds only to the given address. On the MA machine: `curl http://campaign-agent:8787/ma/v1/health` returns `ok` |
| 3. `GET /campaign/active` with ETag and long poll | CA repo | CI tests with a throwaway PostgreSQL: nothing live gives `204`; publishing gives `200` with the right sha256; unchanged gives `304`; the long poll returns when a version is published |
| 4. Migration `0017`, `POST /events`, `ma_events` in the view | CA repo | CI tests: idempotent on `event_id`; invalid enums rejected; secret-like text rejected; a `resolved` event closes its blocker; the dashboard role can select and nothing else |
| 5. MA client: pickup with fallback and cache, sha256 check, status row | MA repo (with M8 items 1 to 6) | Publish a valid campaign: the MA's query output changes at the next pass. Publish an invalid one (hand-inserted): the MA keeps the last good one and reports `MA_CAMPAIGN_REJECTED` |
| 6. MA client: outbox and event sender, first event codes | MA repo | Stop the CA, cause a blocker, start the CA: the event arrives once |
| 7. Failure drills | both machines | Restart the router, stop `tailscaled`, rotate the token, kill each service: results match section 11, and the recovery times are written into the runbook |
| 8. Optional: early notice by long poll | MA repo | `applied` turns `yes` within a minute of a publish with the pipeline running, and a campaign never switches mid-pass |

## 13. Open decisions

1. **Port and URL scheme.** 8787 over plain HTTP inside Tailscale, or HTTPS with `tailscale serve`. Recommendation: plain
   HTTP first.
2. **Campaign pickup through the link or the database as the first choice.** This document makes the link first and the
   database the fallback. If you prefer the database first, the link only carries events.
3. **Where the MA's applied status goes.** Today it is the `ma_campaign_status` row. Keep that (recommended, the CA already
   reads it) and send the same facts as events too, or move it to the link only.
4. **Slack for critical events.** Recommendation: the MA posts them itself. Confirm the channel.
5. **Token rotation** policy and who does it.
6. **Several MA machines later.** One token per MA and a per-machine tag would be needed; not designed here.
7. **Dependence on Tailscale's service.** Acceptable for now; Headscale (self-hosted) is the exit if it stops being free.

## 13a. Changes from the 2026-10-17 audit

- **The Marketing Agent runs only an approved campaign.** With nothing live, no link configured, or the CA unreachable and
  no cached campaign, a discovery pass is skipped (`MA_NO_CAMPAIGN` blocker). `MA_REQUIRE_CAMPAIGN=false` on the MA
  restores the old fallback to its base config.
- **Leads remember their campaign.** New leads and their emails carry `source_campaign_id` / `source_campaign_version`;
  an approved email or follow-up of a campaign that is not live waits (it is not failed).
- **`include_countries`** is a top-level campaign key (it replaces; present only when set). The MA rejects a company its
  page or country domain places elsewhere and keeps, with a note, one whose country it cannot tell. Deploy the MA
  first: an older MA refuses a campaign with this key ("a campaign may not set this key").
- **Level 6 is real.** The MA now has `python -m agent leads queries --campaign FILE` (no database, no network), and CI
  runs level 6 against a checkout of the real MA (`tests/e2e/test_real_ma.py`, job "Contract with the real Marketing
  Agent"). Set `MA_REQUIRED=true` on the CA so nothing is approved that the MA's loader has not accepted.
- **A deleted campaign is never served** (`campaign.deleted_at`), and the worker switches it off if it was live.

## 14. Settings

On the CA machine (`.env`, sent with `bash deploy/remote.sh push-env`; apply migration `0017` first with `deploy/remote.sh migrate`):

| Key | Default | Meaning |
|---|---|---|
| `MA_LINK_ENABLED` | `false` | Start the listener beside the worker |
| `MA_LINK_BIND` | `tailscale` | The machine's Tailscale address, or one literal address. `0.0.0.0` and `::` are refused |
| `MA_LINK_PORT` | `8787` | Listener port (1024 to 65535) |
| `MA_LINK_TOKEN` | none | At least 32 characters. Without it the link stays off, and the service says so on stderr |
| `MA_LINK_TOKEN_PREVIOUS` | none | Still accepted, for one token rollover |
| `MA_LINK_RESPECT_SCHEDULE` | `false` | `true`: the live campaign is only given to the Marketing Agent while it is switched on (`is_active`) and today is between its start and end dates; otherwise "no campaign is live" |

On the MA machine (`.env`, sent with `bash deploy/remote.sh push-secrets`):

| Key | Default | Meaning |
|---|---|---|
| `MA_LINK_URL` | none | `http://campaign-agent.<tailnet>.ts.net:8787/ma/v1`. Without it (or without the token) the link is off and the MA behaves exactly as before |
| `MA_LINK_TOKEN` | none | The same secret as on the CA machine |
| `MA_LINK_CACHE_DIR` | `.cache/campaign` | Last good campaign (`active.json`), the event outbox (`outbox.jsonl`), link state |
| `MA_LINK_TIMEOUT_S` | `10` | Per call |
| `MA_LINK_SEND_EVERY_S` | `5` | How often queued events are sent |

## 15. Checking it on the office machines

After Tailscale works ([tailscale-setup.md](tailscale-setup.md)), in this order:

1. CA: apply `0017`, set the three `MA_LINK_*` keys, `deploy up`. `deploy/remote.sh logs` shows `link listening` with the Tailscale address.
2. From the MA machine: `curl -s -H "Authorization: Bearer $MA_LINK_TOKEN" http://campaign-agent.<tailnet>.ts.net:8787/ma/v1/health` returns `{"status":"ok",...}`. Without the header it must return `401`.
3. MA: set the two keys, `up`. Nothing live gives `204`; the MA runs its base config as before.
4. Approve a campaign (`bash deploy/remote.sh try "..."`, then approve). At the next pass the MA log shows `Campaign applied`, the dashboard view shows `applied: true`, and an `MA_CAMPAIGN_APPLIED` event appears in `ma_events`.
5. Publish a bad version by hand (a key the MA does not know): the MA keeps the last good one, and `MA_CAMPAIGN_REJECTED` is an open blocker in the view.
6. Stop the CA service, wait for a pass, start it: the queued events arrive once.

## 16. The complete leadgen.yaml (option A)

Besides the overlay, `GET /campaign/active` now carries the **whole file** the Marketing Agent would run:

| Key | What it is |
|---|---|
| `leadgen_yaml` | The complete `leadgen.yaml`: the base file with the campaign applied, same sections as the original (`compiler/complete.py`). |
| `leadgen_sha256` | Hash of that text. |
| `base_sha256` | Fingerprint of the base file it was built on (a hash of the parsed data, so comments and spacing do not matter). |

- It is built from the **raw** base text, not from the contract's model, so every value outside the campaign is exactly the base's. It is
  validated with the full contract before it is offered. If it cannot be built, only the overlay is sent (older behaviour).
- The ETag now ends with the first 8 characters of `base_sha256`, so re-syncing the CA's copy of the base makes the agent fetch again.
- The base is a snapshot of the Marketing Agent's `config/leadgen.yaml` (`contract/model.py: BASE_CONFIG_SOURCE`). To re-sync it, copy the file
  to `contract/base_config.yaml` and update the record.
- The Marketing Agent runs the complete file **only if it equals its own base plus the campaign** (see `docs/campaign-link.md` there). Otherwise it
  reports `MA_BASE_DRIFT` and applies the campaign on its own base. A complete file can therefore never undo an edit made on the Marketing Agent.
