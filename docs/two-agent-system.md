# The Two-Agent System: How It Fits Together, How to Run It, What Is Verified

This is the one page for the whole workflow: dashboard → Campaign Agent (CA) → Marketing Agent (MA) → Slack → Gmail. It
links to the detailed documents instead of repeating them. State on branch `fix/audit-findings` (CA and MA), 2026-10-17.

## 1. The workflow, stage by stage

| Stage | Where | What enforces it |
|---|---|---|
| A. Campaign submitted | Dashboard inserts `public.campaign`, then `POST /v1/campaigns/{id}/requests` (`interpret`) | Gateway: bearer token, the person's own Supabase token (`X-User-Token`), strict body schema, idempotency key per campaign. Auto-start picks up campaigns whose `interpret` call was lost (migration 0021) |
| B. Research and proposal | CA worker (`jobs/manager.py`, `jobs/handlers.py`) | One model call → IR → questions or a compiled, validated, frozen version (`campaign_version`, sha256). Validation levels 1-6; level 6 runs the real MA loader. Web research is optional and off by default (`RESEARCH_ENABLED`); it only suggests, a person accepts |
| C. Approval and edits | Dashboard → `approve` / `edit` / `discard` / `deactivate` requests | State machine (`conversation/state_machine.py`); an approval names the exact `version` and `sha256`, stale ones are refused (`stale_approval`); `ca_publish()` re-checks in the database and records who and when; migration 0025 stops any role from inserting a pre-approved version |
| D. Handoff | CA link listener on Tailscale (`ma_link/`), MA client (`agent/campaign_link.py`) | Only an approved, live, non-deleted version is served. Bearer token (with rollover), ETag long poll, sha256 and shape checks, the CA's complete file must equal the MA's own merge, atomic cache, events back through a bounded outbox with retries. Repeated pickups record one `MA_CAMPAIGN_APPLIED` |
| E. MA execution | MA service (`agent/supervisor.py`) | A discovery pass runs only for a live approved campaign (`MA_REQUIRE_CAMPAIGN`, default true). What it runs is reported in `ma_campaign_status`; the CA shows `applied` only when id, version and sha256 match |
| F. Discovery and qualification | `agent/leadgen/pipeline.py`, browser backends | Rules first, one model call per page, quotes must appear on the page, website never guessed, campaign countries enforced, robots.txt and public-address checks before every page, CAPTCHAs never solved. States `DISCOVERED → QUALIFIED → CONTACT_FOUND → EMAIL_DRAFTED → PENDING_APPROVAL → APPROVED → SENT`. Each lead and email carries the campaign id and version |
| G. Persistence | Shared Supabase Postgres | Unique domain per company, unique `(contact, campaign, step)` per email, one pending approval per draft, idempotent link events, row locks on the send claim |
| H. Drafts | `agent/leadgen/outreach.py` | Contacts only from the company's own site, MX check before drafting, review notes when checks flag something |
| I. Slack approval | MA posts the card; Supabase Edge Function `slack-interact` receives the click | Slack signature and 5-minute window, allow-list, `decide_email()` decides once, refuses expired requests, stores a sha256 of recipient + subject + body; decided emails and their contact address are frozen by triggers (MA migration 0014). Every card shows the full text |
| J. Sending | `agent/leadgen/sender.py` (the only send path) | Claim query requires the approval and a matching hash; re-check; daily cap; suppression list; campaign must still be live; `TEST_RECIPIENT` refused in prod and recorded as `test_sent`. **Never twice:** the Gmail send is not retried by the client library; an unclear result (timeout, 5xx) is held as `sending` and settled from Gmail's Sent folder by its fixed Message-ID before any new attempt |

The dashboard reads one `workflow_status` per campaign (`RECEIVED`, `RESEARCHING`, `NEEDS_INPUT`, `CHANGES_REQUESTED`,
`PENDING_CAMPAIGN_APPROVAL`, `APPROVED`, `REJECTED`, `CONFIG_READY`, `DELIVERED_TO_MA`, `PROCESSING`, `FAILED`, `CANCELLED`)
and per-campaign lead and email counts: see [dashboard-api.md](dashboard-api.md#workflow-status).

## 2. Contracts

| Contract | Defined in | Checked by |
|---|---|---|
| Dashboard → CA gateway | [dashboard-api.md](dashboard-api.md), `api/schemas.py` | `tests/unit/test_api_gateway.py`, `tests/integration/test_dashboard_gateway.py` |
| The campaign file (overlay, `config_version` 1.0) | `contract/model.py` (`CampaignOverlay`, `LeadgenFile`) | CA level 6 against the real MA (`tests/e2e/test_real_ma.py`); the MA's `check_overlay` |
| CA ↔ MA link (HTTP over Tailscale) | [ca-ma-link.md](ca-ma-link.md) | both repositories' link tests; `tests/e2e/test_two_agents.py` |
| What the MA runs | `ma_campaign_status` (CA migration 0016) | `refresh_applied`, the two-agent test |

## 3. Databases and migrations

One Supabase project holds the dashboard's `campaign` table, the CA's tables and the MA's tables. Order:

1. **MA first** (an older MA refuses campaigns that use `include_countries`): `python -m agent migrate` with the owner URL
   (adds 0014 approval binding and campaign attribution, 0015 the `marketing_agent` role).
2. **CA:** `bash deploy/remote.sh migrate --plan`, then `bash deploy/remote.sh migrate` (0025 and the repeatable files:
   the MA pipeline grants and the per-campaign progress counts, applied again on every run, so order mistakes fix
   themselves).
3. **Dashboard:** `dashboardbackend/migrations/007_dashboard_backend_role.sql` in the Supabase SQL editor.
4. Switch the MA and dashboard backend logins from the owner to their new roles; rotate the owner password.

Details: [runbook.md](runbook.md) (CA), the MA's `deploy/README.md`.

## 4. Settings

Real values live only in each machine's git-ignored `.env`. The CA's settings are all in `src/campaign_agent/settings.py`;
the ones that matter for this workflow:

| Setting | CA machine | Note |
|---|---|---|
| `DATABASE_URL` | `campaign_agent` role | never the owner |
| `MIGRATE_DATABASE_URL` | owner, only while migrating | remove afterwards |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_S`, `LEASE_S` | Ollama on the machine | the lease is renewed while a request runs |
| `MA_CHECKOUT`, `MA_REQUIRED=true` | a checkout of the MA | level 6 must run in production |
| `MA_LINK_ENABLED=true`, `MA_LINK_BIND=tailscale`, `MA_LINK_PORT=8787`, `MA_LINK_TOKEN` | the link | token: 32+ random characters, same value on the MA |
| `API_DATABASE_URL`, `API_TOKEN`, `API_SUPABASE_URL`, `API_REQUIRE_USER` | Render (gateway) | `render.yaml` |

MA machine (its README, "Settings"): `DATABASE_URL` (`marketing_agent` role), `MA_LINK_URL=http://<CA Tailscale
name>:8787/ma/v1`, `MA_LINK_TOKEN`, `MA_REQUIRE_CAMPAIGN=true`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_OUTREACH`,
`SLACK_ALLOWED_USERS`, `GMAIL_CREDENTIALS_PATH`, `GMAIL_TOKEN_PATH`, `MAIL_FROM`, `EMAIL_SENDING_ENABLED`, `APP_ENV=prod`,
`DAILY_SEND_CAP_PER_MAILBOX`, `COMPANY_NAME`, `COMPANY_ADDRESS`, `UNSUBSCRIBE_BASE_URL`, `UNSUBSCRIBE_SECRET`. The service
unit sets `MA_ON_OFFICE_MACHINE=1`; production commands refuse to run without it.

Both agents check their settings at start and stop with one sentence when something critical is missing
(`python -m campaign_agent start`, `python -m agent check`).

## 5. Running each agent on its own machine

**CA (office machine 1)**, from the laptop: `bash deploy/remote.sh install-service`, `bash deploy/remote.sh up`,
`bash deploy/remote.sh status`, `bash deploy/remote.sh logs`. It runs `python -m campaign_agent start` as the systemd user
service `deploy/campaign-agent.service` (worker, heartbeat, and the link listener when `MA_LINK_ENABLED=true`). Full
procedure, upgrades and rollback: [runbook.md](runbook.md).

**MA (office machine 2)**, from the laptop in the MA repository: `bash deploy/remote.sh up` (refreshes and restarts
`marketing-agent.service`, which runs `python -m agent start`), then `bash deploy/remote.sh agent check`. First install:
the MA's `deploy/new-machine-steps.md`.

**Gateway:** Render, from `render.yaml` (`python -m campaign_agent api`).

Both services stop cleanly on SIGTERM (the CA within a second; the MA finishes the current page) and restart on failure.

## 6. Tailscale and ports

Both office machines join the tailnet ([tailscale-setup.md](tailscale-setup.md), [ca-ma-link.md](ca-ma-link.md) §4).
Only one port crosses it: **TCP 8787 on the CA machine**, bound to its Tailscale address only (`0.0.0.0` is refused).
The ACL should allow only the MA machine to reach `campaign-agent:8787`. Tailscale is not trusted on its own: every link
call needs the bearer token. Everything else is outbound HTTPS (Supabase, Slack, Gmail, Render). Check from the MA:
`curl -H "Authorization: Bearer $MA_LINK_TOKEN" http://campaign-agent:8787/ma/v1/health`.

## 7. Slack

Set up once (the MA's `supabase/functions/README.md`): a Slack app with a bot token (`SLACK_BOT_TOKEN`) in the approval
channel; Interactivity → Request URL = the `slack-interact` Edge Function; its secrets `SLACK_SIGNING_SECRET` and
`SLACK_ALLOWED_USERS` (the Slack user ids who may approve). Deploy with `supabase functions deploy slack-interact
--no-verify-jwt` (Slack sends no JWT; the function checks Slack's signature itself). A card shows company, contact,
recipient, subject and the whole body; Approve or Skip decides once. An expired card says so and the draft is posted again.

## 8. Email and safe testing

Gmail API as one account (`python -m agent gmail-check` once, which writes `token.json`). Safe ways to test, in order:

1. `--dry-run` on any MA command: its own SQLite file, Slack written to files, mail written to `out/outbox/`.
2. `APP_ENV=dev` with `ALLOWED_RECIPIENT_DOMAINS=<your test domain>`: anything else is refused.
3. `TEST_RECIPIENT=<your address>`: every mail goes to you, marked `[TEST for ...]`, recorded as `test_sent`; refused
   when `APP_ENV=prod`.

Real outreach needs `EMAIL_SENDING_ENABLED=true`, `APP_ENV=prod`, no `TEST_RECIPIENT`, and SPF, DKIM and DMARC on the
sender's domain.

## 9. Tests

All tests run in GitHub Actions, never on a laptop. CA: lint, mypy, unit and integration tests on Postgres, migrations,
secret scan, markdown; the job "Contract with the real Marketing Agent" checks out the MA (same branch name if it exists)
and runs level 6 and **the two-agent end-to-end test** (`tests/e2e/test_two_agents.py`):

request → proposal → edit → new version → stale approval refused → approval (clicked twice, applied once) → link serves
only the approved version (and refuses a missing token) → real MA client picks it up twice, records it once → real
pipeline over a fixed fake web → lead and draft tied to the campaign version → nothing sent before approval → Slack
decision twice (second is `already_decided`) → nothing sent while another campaign is live → exactly one mail in the
outbox, a second round sends nothing → the CA shows `DELIVERED_TO_MA` → the MA restarts and runs again: no new lead,
email or send → the campaign is switched off: `CANCELLED`, and the MA stops running it.

No website, real record or real email is involved: the CA uses its stub model, the MA its own test fakes, its dry-run
Slack approver and its outbox transport.

MA: ruff, mypy, Postgres-backed tests of the SQL safety functions (`decide_email`, the send gate, frozen content, expiry,
campaign gating), and the unclear-send tests in `agent/tests/test_gmail.py`.

## 10. What is not verified, and what needs a person

- **Not run against the real services:** Slack, Gmail, Supabase as deployed, Tailscale between the two office machines,
  Render. The code paths are tested with fakes; the real setup is checked by the soak suite on the office machines
  (`qa/`, `bash qa/run.sh`), which last failed only because the MA's link settings were missing (audit F-01).
- **Needs a person:** set `MA_LINK_URL`, `MA_LINK_TOKEN` on the MA machine and `MA_LINK_ENABLED=true` on the CA; apply
  the migrations in the order above; switch the MA and dashboard logins to their limited roles; set `MA_REQUIRED=true`
  and `MA_CHECKOUT` on the CA machine; set `API_SUPABASE_URL` on Render and, once the dashboard forwards the user token,
  `API_REQUIRE_USER=true`; deploy the dashboard changes (its own repository).
- **Known limits:** company size and preferred contact roles are shown as "not applied" (the MA has no reliable source
  for company size); contacts are checked for a mail-capable domain, not a verified mailbox; "research" is extraction and
  optional suggestions, not an independent study of the market; Gmail gives no delivery receipts, so states are
  `sent` (accepted by Gmail), `bounced` (a bounce arrived), `test_sent`, `expired` (failed after retries), and an unclear
  send stays `sending` until it is settled; there is no "completed" state, a campaign runs until it is switched off.
