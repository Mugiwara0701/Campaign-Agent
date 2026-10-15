# Campaign Agent backend and what the dashboard must do (overview)

One page for the whole picture: what the backend now does, how it fits together, how to put the gateway on Render, and exactly
what the dashboard developer builds in the **server code** and in the **frontend**. The details behind each part are in the
files listed at the end.

## 1. The idea in one picture

```text
 USER ─▶ hosted DASHBOARD ──(HTTPS + token)──▶ GATEWAY on Render ──┐
          (frontend + its server)                                  │ (same tables; the dashboard may also
                                                                   ▼  write them directly, see section 6)
                                                            SUPABASE (database)
                                                                   ▲
                                      outbound polling only        │  every ~2 s
 ┌──────────────── office, behind the router, nothing exposed ─────┴──────────────┐
 │  CAMPAIGN AGENT service (CA)  ──── Tailscale (private) ────  MARKETING AGENT (MA) │
 │  reads campaigns, asks questions,   fetches the live campaign,  does the lead work, │
 │  writes the leadgen file, runs MA   reports events/blockers     Chrome on its desktop │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

- The **CA is the brain.** It starts the MA, stops it, and tells it which leadgen file to use. The dashboard does not control the MA.
- The CA and the MA are on the office network and are **private**. The hosted dashboard never connects to them. Everything
  between the dashboard and the CA goes through the database (the CA only makes outbound connections).
- A person approves every campaign in the dashboard, and every email is approved by a person in Slack. Nothing here changes that.

## 2. What we built in the backend (the CA, repository `Campaign-Agent`)

| Part | What it does | Where |
|---|---|---|
| **Campaign engine** | Reads the campaign text, asks questions, writes and checks the leadgen file (levels L3 to L6), keeps versions | `src/campaign_agent/` (existing) |
| **Link to the MA** | A private API over Tailscale: the MA fetches the live campaign (with a check that it arrived intact) and sends back events and blockers | `ma_link/`, `docs/ca-ma-link.md`, `docs/tailscale-setup.md` |
| **Complete leadgen file** | The CA builds the whole file the MA runs, not only a patch | `compiler/complete.py` |
| **Approval can run the MA** | Approving with `start_pipeline: true` starts the MA (or restarts it, so it takes the new file) after the publish is saved | `jobs/manager.py`, `docs/publishing.md` |
| **Pause and resume** | The dashboard asks; the CA stops taking requests. **Pausing also stops the MA, resuming starts it again only if it was running** | `ca_control`, `docs/dashboard-pause-resume.md` |
| **Automatic start** | A new campaign that has no request is started by the CA itself (checked every 10 s, after 20 s, for 7 days) | `jobs/worker.py`, migration 0021 |
| **Gateway for Render** | A small HTTPS service in front of the same tables, with one shared token | `api/`, `main.py`, `render.yaml`, `docs/dashboard-api.md` |
| **Office window** | A full-screen pixel-art office on the CA machine (CEO box, review and approve, pause and resume, live status). Starts at login | `console_*.py`, `deploy/start-ui.sh` |
| **Schedule rule (optional, off)** | Give the MA the live campaign only while the campaign is switched on and today is within its dates (`MA_LINK_RESPECT_SCHEDULE=true`) | `ma_link/service.py` |

The MA side (fetching the campaign, the events, the blockers) lives in the **other repository, `Marketing-AI-Agent`**, branch
`feature/ca-link`. It is **not committed or pushed yet**; ask for it when you want it in that repository.

### Database changes (migrations)

| Migration | What it adds | Status |
|---|---|---|
| `0013` to `0016` | the CA tables, the worker, publishing, the MA status | applied |
| `0017` | `ma_event` (events and blockers from the MA) | applied |
| `0018` | the CA may start and stop the MA through `request_pipeline` | applied |
| `0019` | `ca_control` and `ca_request_state()` (pause and resume) | applied |
| `0020` | `ma_resume`: remember whether the MA was running when paused | applied |
| `0021` | `ca_auto_interpret()`: automatic start on a new campaign | applied |
| `0022` | one extra read permission for the gateway | **apply it** (step 1 of section 4) |
| `migrations/optional/dashboard_auth.sql` | sign-in rules if the browser writes to the database directly | optional, apply by hand, not applied |

Apply with `bash deploy/remote.sh migrate --plan`, then `bash deploy/remote.sh agent migrate --yes`.

### What was tested

751 unit tests pass (run on the office machine) and the linter is clean. The database tests (they run every call as the real
limited roles) need a throwaway local PostgreSQL and run in CI; they have not been run yet. These things were also run for
real against the office machines and the live database: the link between the two machines, a campaign built and approved,
the MA picking it up and reporting a blocker, pause and resume, and the automatic start. The gateway has **not** been run on
Render yet.

## 3. How a campaign flows

1. **The user types the campaign** in the dashboard. The dashboard saves the `campaign` row (as it does today).
2. **The CA starts.** The dashboard sends an `interpret` request (instant), or the CA notices the new campaign by itself
   (within about 30 seconds). The model reads it; this takes a few minutes on the office machine.
3. **The CA asks questions** if something is missing. The dashboard shows them; the user answers (`answer`).
4. **The CA writes the leadgen file** and checks it. The state becomes `AWAITING_REVIEW`. The dashboard shows the review.
5. **The user approves, asks for a change, or rejects** (`approve`, `edit`, `discard`). To change something, the CA makes a new version.
6. **On approval the campaign is live.** With `start_pipeline: true` the CA also starts the MA on it.
7. **The MA works.** It fetches the file, uses it for its pass, and sends back events. A problem it cannot solve becomes a
   **blocker**, which the dashboard shows until the MA reports it fixed.
8. **Pause** (any time): the CA takes no new request and the MA stops; **resume** starts the MA again if it was running.

## 4. Put the gateway on Render (every step)

The gateway is optional (the dashboard can write the database itself), but this is the way to give the dashboard plain HTTPS
calls. It is a small Python web service from this repository.

### Before Render

1. **Apply migration 0022:** `bash deploy/remote.sh migrate --plan`, then `bash deploy/remote.sh agent migrate --yes`.
2. **Give the limited dashboard role a login:** `bash deploy/remote.sh agent dashboard-password`. It asks for a password
   (20 to 100 characters from `A-Z a-z 0-9 . _ ~ -`, not shown) and prints the `API_DATABASE_URL` shape:
   `postgresql://campaign_dashboard.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`.
   Use Supabase's **pooler** address (port 6543). Render cannot reach the direct database address.
3. **Make a token:** `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. This is `API_TOKEN`. Keep it secret.
4. **Get the code onto the branch Render will use.** It is pushed to `feature/ma-link`. Either merge it to `main` (a pull
   request), or choose that branch when you create the Blueprint below.

### On Render

1. Sign in at render.com, connect your GitHub, and allow it to read the `Campaign-Agent` repository.
2. **New > Blueprint**, choose the repository (and the branch). Render reads `render.yaml`: a free web service named
   `campaign-agent-gateway`, build `pip install .`, start `python -m campaign_agent api`, health check `/health`, Python 3.12.7.
3. Render asks for the two secrets. Paste `API_DATABASE_URL` (from "Before Render", step 2) and `API_TOKEN` (step 3).
   Click **Apply**.
4. Wait for the first deploy (a few minutes). Render shows a URL like `https://campaign-agent-gateway.onrender.com`.

### Check that it works

1. `curl https://<your-service>.onrender.com/health` returns `{"status":"ok"}`.
2. `curl -H "Authorization: Bearer <API_TOKEN>" https://<your-service>.onrender.com/v1/ca` shows `"online": true` when the CA
   is running on the office machine. Without the header it returns 401.
3. If it returns 503 "a migration is missing", migration 0022 or the login ("Before Render", step 2) was not done. If it does not
   start, open the Render logs: it prints that `API_DATABASE_URL` and `API_TOKEN` must be set.

### Where it is deployed

The gateway is live at `https://campaign-agent-maha.onrender.com` (health: `/health`). The token is held by the owner and is
given to the dashboard developer separately; it is not written in any file in this repository.

### Give it to the dashboard

1. In the dashboard's **server** environment set `CA_GATEWAY_URL` (the Render address) and `CA_GATEWAY_TOKEN` (the same token).
   Never in browser code.

### Know this

- Render's **free plan sleeps** after about 15 minutes without a call; the next call takes 30 to 60 seconds. The dashboard's
  server should allow that wait, or use a paid plan.
- To change the token: change it in Render and in the dashboard's server together.
- A redeploy happens by itself on each push to the chosen branch (Render's default).

## 5. What the dashboard developer does

The campaign form itself does not change. Everything below is for the review, approval and control screens. The calls are in
`docs/dashboard-api.md` (HTTP) and the same requests in SQL in `docs/dashboard-integration.md`.

### Dashboard backend (its server code)

1. Add settings `CA_GATEWAY_URL` and `CA_GATEWAY_TOKEN`. Keep them on the server.
2. After saving a campaign, call `POST /v1/campaigns/{id}/requests` with `interpret` (optional: the CA also starts by itself).
3. Add routes the frontend can call, each passing through to the gateway and **always filling `requested_by` from the signed-in
   user, never from what the page sent**:
   - send a request (`answer`, `edit`, `suggestion`, `approve`, `discard`, `rollback`, `deactivate`),
   - read a campaign (`GET /v1/campaigns/{id}`), read a request's status (`GET /v1/requests/{id}`),
   - read the CA (`GET /v1/ca`), pause and resume (`POST /v1/ca/state`).
4. **Remove** the MA Start and Stop routes (the pipeline API). The CA runs the MA now.
5. Allow for a slow first call after the gateway slept (a timeout of about a minute).

### Dashboard frontend

1. **The review and approval page** (layout in `docs/dashboard-integration.md`, section 2): the questions, the fields and where
   each came from, "could not be applied" (always shown), the checks L3 to L6, the leadgen file, the fixed email sentence, and the
   buttons **Approve**, **Request a change**, **Reject** (`discard`, with a confirmation).
2. **Approve sends exactly the version and hash the user saw**, with `start_pipeline: true` if approval should run the MA.
3. **The CA badge and Pause / Resume button**: show `online`, `paused`, and both `desired_state` and `actual_state`
   ("pausing...", "resuming...").
4. **The MA panel (read only)**: `applied`, `ma_error`, and every open blocker (`ma_events`) as an alert.
5. **Remove** the MA Start and Stop buttons.
6. Handle the errors in `docs/dashboard-integration.md`, section 5 (`stale_approval`, `stale_revision`, `publish_blocked`, `rate_limited`, ...).
7. Show "the assistant is offline" when `GET /v1/ca` says `online: false`, never an endless spinner.

### What the dashboard must not do

- Do not put the token or the database login in browser code, or call the gateway from the browser.
- Do not send an approval for something the user did not see, or take `requested_by` from the page.
- Do not hide "could not be applied", a failed check, a skipped L6, or an open MA blocker.
- Do not keep its own copy of the campaign; render `review_view` and read it again after every request.
- Do not control the MA (no Start or Stop). Do not promise that approving sends email (every email needs approval in Slack).

## 6. Two ways to reach the CA

| | Through the gateway | Straight to the database |
|---|---|---|
| How | HTTPS calls with a token (section 4) | the dashboard's server inserts rows and reads tables (`docs/dashboard-integration.md`) |
| Needs | the Render service | the dashboard's own database access |
| Same requests and states | yes | yes |

If the **browser** writes to the database directly with Supabase Auth, use `migrations/optional/dashboard_auth.sql` so the
database itself checks who is signed in. Turn off public sign-ups in Supabase Auth first.

## 7. Operating it (the office machines)

- **CA service:** `campaign-agent` (systemd user service, starts at boot). `bash deploy/remote.sh status`, `logs`, `up`.
- **Office window:** opens full screen at login (`deploy/start-ui.sh`); F11 leaves full screen, Alt+F4 closes it.
- **A campaign does not start:** check `GET /v1/ca` (offline? paused?), then `Requests` in the window's status column
  (pending means the CA has not taken it), then `journalctl --user -u campaign-agent`.
- **Pause is stuck:** `select * from ca_control;` shows what was asked and what the CA does.
- **The MA says "stuck":** read the open blockers in `ma_events`; the window shows them too.

## 8. Not built yet (decisions needed)

- Stopping **only** the MA, without pausing the CA (it would be a request handled by the CA).
- Letting the dashboard read "is the MA running right now" (a small read-only permission).
- A country filter in the leadgen file: the file format has no country include list yet, so the CA reports it as "could not be applied".
- More than one campaign live at once.
- The MA repository's changes are not committed or pushed.

## Files

| File | For |
|---|---|
| `docs/backend-and-dashboard-guide.md` (this) | the whole picture |
| `docs/dashboard-integration.md` | what to build, step by step, and what not to do |
| `docs/dashboard-api.md` | the gateway: calls, examples, Render |
| `docs/dashboard-pause-resume.md` | pause and resume |
| `docs/handover.md`, `docs/store-and-inbox.md`, `docs/publishing.md` | the request contract, errors, approval |
| `docs/ca-ma-link.md`, `docs/tailscale-setup.md` | the private link between the CA and the MA |
| `render.yaml` | the Render service |
