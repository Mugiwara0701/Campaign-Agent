# Functional Verification Report: Campaign Agent and Marketing Agent

Date: 2026-10-17. Branch `fix/audit-findings` in both repositories:

| Repository | What it is | Commit verified |
|---|---|---|
| `Mugiwara0701/Campaign-Agent` | Campaign Agent (CA): worker, dashboard gateway, Tailscale link listener | `3004ced` (CI run 39021756275) |
| `Mugiwara0701/Marketing-AI-Agent` | Marketing Agent (MA): lead pipeline, Slack approval, Gmail sending | `d3e7c82` (CI runs 39022260408 tests, 39022260446 security); `829d66e` formatting only |
| `shivtf/Marketing_Agent` (on the laptop) | Dashboard: read for its contract; not changed, not pushed | `fix/audit-findings`, uncommitted changes from the earlier session |

**How things were run.** Nothing ran on the laptop: by standing rule, every test, linter and type check ran in GitHub
Actions on a throwaway PostgreSQL. Results were read through GitHub's public checks API (each repository reports test
failures as annotations). Neither office machine was touched, no real Slack message was posted and no email was sent.

Inputs: the earlier audit `docs/audit-2026-10-17.md` (findings F-01 to F-30) and the system overview
`docs/two-agent-system.md`.

---

## A. Overall assessment

**The code of both agents implements every stage of the workflow, and the stages are joined.** The decisive evidence is
an automated end-to-end test, `tests/e2e/test_two_agents.py`. It runs the real CA and the real MA code together, over HTTP
on the CA's link listener: from a dashboard request through an edit, a stale approval, the right approval, delivery,
discovery, qualification, a Slack-style approval and one email in the outbox. It then covers the MA restarting and the
campaign being switched off.

**The system is not fully verified, and it is not ready for real outreach.** Four things were never exercised:

- the real external services (Slack, Gmail, Chrome on the office machine, Tailscale between the two machines, Supabase as deployed);
- the dashboard's side of the contract;
- real web research;
- company-size targeting.

The last two are not implemented (research only partly). Section H answers the verdict questions one by one.

---

## B. Requirement verification matrix

Statuses: **PASS** implemented and verified by a test that ran in CI; **PARTIAL** some of it exists or is verified;
**FAIL** a defect or a missing implementation; **BLOCKED** needs something not available here.

### Campaign Agent

| ID | Agent | Requirement | Status | Evidence | Remaining issue |
|---|---|---|---|---|---|
| CA-01 | CA | Campaign reception | PASS | `api/routes_campaigns.py`, `api/schemas.py` (strict body, 64 KB cap, `extra="forbid"`), `api/service.py`; idempotency key unique per campaign; 401/403/404/422/503 with a fixed `{"detail"}` shape. Tests: `tests/unit/test_api_gateway.py`, `tests/integration/test_dashboard_gateway.py` (as the real limited DB role), two-agent test step "request" | The campaign row and its id (`gen_random_uuid()`) are created by the dashboard in `public.campaign`; the CA receives the `interpret` request. Fields are free text (`campaign_detail`), not structured industry/location/size fields |
| CA-02 | CA | Campaign research | PARTIAL | One model call turns the text into a validated IR (`extractor/`), gaps become questions (`gap/`, `questions/`), the compiler and validators (levels 1-6) check the result. Optional web research (`research/`: SearXNG, domain policy, public-IP check on every redirect) produces suggestions a person must accept. Tests: `test_extractor.py`, `test_research.py`, `test_gap_engine.py`, `test_validators.py` | No independent research of the market or target companies; `RESEARCH_ENABLED` is off by default; the proposal is a targeting configuration, not an evidence-backed company profile. No fake or hard-coded research output was found: when research is off or fails, nothing is claimed |
| CA-03 | CA | Proposal and dashboard communication | PASS | `GET /v1/campaigns/{id}` returns `state`, `workflow_status`, `error`, `revision`, `review_view` (candidate version, sha256, YAML, what is not applied, validation levels, questions) and `execution`; `GET /v1/campaigns` lists campaigns. Tests: `test_workflow_status.py`, `test_dashboard_gateway.py::test_the_workflow_status_and_the_list_as_the_dashboard_role`, two-agent test (status checked at each step) | Dashboard side not run (section C) |
| CA-04 | CA | Approval and editing | PASS | State machine `conversation/state_machine.py`; approval must name `version` + `sha256` (`jobs/handlers.py::_approve`), re-checked by `ca_publish()` in the database (migration 0015); migration 0025 forbids inserting a pre-approved version; `approved_by` / `approved_at` recorded; duplicate request keys return the first request. Tests: `test_state_machine.py`, `test_publish.py` (stale approval), `test_roles.py`, `test_audit_fixes.py` (0025), two-agent test (edit makes version 2, approving version 1 fails `stale_approval`, double approval is one request) | Reviewer identity is the signed-in person only once `API_REQUIRE_USER=true` on Render and the dashboard forwards `X-User-Token`; until then the dashboard server's `requested_by` is trusted |
| CA-05 | CA | Lead-generation configuration | PASS | `contract/model.py` (`CampaignOverlay`, strict, `config_version` 1.0), `compiler/`; the link payload carries `campaign_id`, `version`, `sha256` and the complete file; only approved versions are served (`repositories.live_version_for_ma`: `approved_at is not null`, not deleted). Tests: `test_compiler.py`, `test_complete_file.py`, `tests/e2e/test_real_ma.py` (the real MA loader accepts a compiled campaign and refuses a forbidden key), two-agent test (link answers 204 before approval) | Company size is reported as "not applied": the MA cannot enforce it. Preferred contact roles are applied (`preferred_roles`, a closed vocabulary; follow-up below). The approval reference is the version's `approved_by` / `approved_at` in the CA database; it is not copied into the file |
| CA-06 | CA | Delivery to MA | PASS (local) / BLOCKED (Tailscale) | Pull model: the MA polls `GET /ma/v1/campaign/active` (bearer token with rollover, ETag long poll, binds the Tailscale address only, `0.0.0.0` refused); the MA reports what it runs (`ma_campaign_status`) and sends events (`POST /ma/v1/events`, idempotent by `event_id`). Delivery state as the CA sees it: `CONFIG_READY` (live, not confirmed) → `DELIVERED_TO_MA` (the MA confirmed this id, version and sha256) or `FAILED` (the MA refused it, with its reason). Tests: `test_ma_link.py`, `test_ma_link_store.py`, two-agent test (MA picks up twice, one `MA_CAMPAIGN_APPLIED`; approved while the MA was not running, delivered when it ran) | Real two-machine Tailscale traffic not verified here. The soak run of 2026-10-16 failed this step because the MA machine had no `MA_LINK_URL` / `MA_LINK_TOKEN` (F-01): a person must set them |

### Marketing Agent

| ID | Agent | Requirement | Status | Evidence | Remaining issue |
|---|---|---|---|---|---|
| MA-01 | MA | Configuration reception | PASS | `agent/campaign_link.py`: token, sha256 check, allowed keys and types (`check_overlay`), the CA's complete file must equal the MA's own merge (`resolve`), atomic cache on disk, `applied` / `rejected` events and `ma_campaign_status`. Tests: MA `test_campaign_link.py`, `test_audit_fixes.py`; CA `test_real_ma.py`; two-agent test (real client over HTTP, ETag "unchanged" on the second pickup) | The received campaign is kept in the MA's cache file and status row, not in a campaign table of its own |
| MA-02 | MA | Campaign execution | PASS (code) / BLOCKED (service on its machine) | `agent/control.py` runs one pass at a time behind a lock; `leadgen/service.run_leads` refuses to run without a live approved campaign (`MA_REQUIRE_CAMPAIGN`, blocker `MA_NO_CAMPAIGN`); lead states are stored and resumed. Tests: `test_supervisor.py`, `test_control.py`, `test_audit_fixes.py` (no campaign, no pass), `test_leadgen_pipeline.py::test_crash_after_contact_is_resumed_without_duplicates`, two-agent test (restart: no new lead, no new email) | The two-agent test drives `Pipeline` directly; the long-running service (`python -m agent start`) is not run in CI. A second process started by hand on the office machine (`leads run`) is not locked out by the service |
| MA-03 | MA | Chrome and browser automation | BLOCKED | Three real backends exist: `browser/http.py` (SearXNG + HTTP), `browser/chrome.py` (Playwright Chrome), `browser/desktop.py` (visible Chrome via xdotool and OCR, used in production). Every page passes `web.may_visit` (blocked portals, public address only, robots.txt); redirects are checked hop by hop; CAPTCHAs are never solved. Tests: `test_leadgen_browser.py`, `test_desktop.py`, `test_web_rules.py` | A real browser on the office machine was not run. The soak run of 2026-10-16 passed `ma.search.discovery` on that machine, but that run is before these changes |
| MA-04 | MA | Lead qualification | PASS | `leadgen/qualify.py` (rules first, then one model call; quotes must appear on the page; website never guessed; score with penalties; campaign countries enforced, unknown country kept with a note), dedup by domain and name, separate states `DISCOVERED`, `QUALIFIED`, `CONTACT_FOUND`, `EMAIL_DRAFTED`; source URLs and evidence per lead. Tests: `test_leadgen_qualify.py`, `test_leadgen_intent.py`, `test_leadgen_pipeline.py`, two-agent test (of four fake results, the shop page, blocked site and distributor never became leads; one qualified) | Company size is not enforced (no reliable source). Contact-role preference is applied since `f8212c8` (people in the campaign's roles are contacted first). Contacts are checked for a mail-capable domain (MX/A), not a verified mailbox |
| MA-05 | MA | Database persistence | PASS | Migrations 0001-0015; unique company domain, unique `(contact, campaign, step)` email, one pending approval per draft; leads and emails carry `source_campaign_id` / `source_campaign_version` (0014). Tests: `test_leadgen_postgres.py` on PostgreSQL in CI (decide once, frozen content, expiry, campaign gating), two-agent test (lead and email tied to campaign version 2; records survive a restart) | Production database grants as deployed not checked; the `marketing_agent` role (0015) is not yet in use (F-06) |
| MA-06 | MA | Email draft creation | PARTIAL | Drafts only for qualified leads with a contact from the company's own site; subject and body stored; draft linked to contact, company and campaign; drafts flagged in a review note when checks fail. Tests: `test_leadgen_outreach.py`, `test_leadgen_pipeline.py`, two-agent test | A draft is one row, frozen once decided. Since `cabd094` every version shown is identified: the card shows its fingerprint, the pending request stores it (`shown_sha256`), and a decision on a draft that changed after its card was posted is refused (`changed`) and the new version is posted. The text of an earlier, never-decided version is not kept. Text is written by a model from the page's facts; it is checked, not proven free of unsupported claims |
| MA-07 | MA | Slack approval | PARTIAL (code PASS, Slack BLOCKED) | The card shows company, recipient, subject, the whole body, and now the campaign (id and version) and the draft (id and content fingerprint). `slack-interact` checks Slack's HMAC signature with a 5-minute window and the reviewer allow-list; `decide_email()` decides once, refuses expired requests and stores the content hash. Tests: `test_leadgen_safety.py` (rejected never sent, expired, duplicate decision, card contents, one draft's approval cannot authorize another, changed body or recipient not sent), `test_leadgen_postgres.py`, two-agent test (decision twice: `approved`, `already_decided`) | Since `959ed89` the Edge Function's logic is in `handler.ts` with Deno tests (`handler_test.ts`, run in CI). Real Slack posting and clicks not exercised |
| MA-08 | MA | Email delivery | PARTIAL (gate PASS, Gmail BLOCKED) | One send path: claim requires an approval whose hash matches recipient + subject + body; re-check; daily cap; suppression; campaign must be live. Gmail send never retried blindly: an unclear result stays `sending` and is settled from the Sent folder by a fixed Message-ID. States: `approved`, `sending`, `sent` (accepted by Gmail), `test_sent`, `bounced`, `expired` (failed); Gmail gives no delivery receipt, so "delivered" is not claimed. Tests: `test_gmail.py` (unclear result held, found → sent, not found → one attempt used, Gmail unreachable → held, 4xx, 429, no client retry, fixed Message-ID), `test_leadgen_safety.py`, two-agent test (nothing before approval, nothing while another campaign is live, exactly one mail, equal to what was approved) | Real Gmail not exercised; sender domain SPF/DKIM/DMARC not checked |

### Integration

| ID | Agent | Requirement | Status | Evidence | Remaining issue |
|---|---|---|---|---|---|
| INT-01 | Both | Endpoints, methods, schemas, field names | PASS | Two-agent test uses the real MA client against the real CA app; CA level 6 runs the real MA loader | — |
| INT-02 | Both | IDs and versions consistent | PASS | Two-agent test: MA reports `(campaign_id, version) == (cid, 2)`; lead and email carry `[cid, 2]`; CA shows `DELIVERED_TO_MA` only on an exact sha256 match | — |
| INT-03 | Both | Authentication | PASS | Two-agent test: request without a token → 401; CA link tests (token rollover) | — |
| INT-04 | Both | Timeouts, retries, acknowledgement, duplicates | PASS | MA link client: `MA_LINK_TIMEOUT_S`, outbox with bounded backoff; events idempotent; two-agent test (pickup twice, one event) | — |
| INT-05 | Both | Recovery from interruptions | PASS (local) | Approved while the MA was not running → delivered when it ran; MA restart → resumes, no duplicates; CA `test_acceptance.py::test_published_while_the_marketing_agent_is_offline_is_applied_when_it_returns` | Real network interruption on Tailscale not exercised |
| INT-06 | Both | Real Tailscale between two machines | PARTIAL | Setup documented (`docs/tailscale-setup.md`, `docs/ca-ma-link.md`) | Link verified on the office machines on 2026-10-17 (health, 204, 401; section after E). A full campaign over it not yet run |
| INT-07 | Dashboard ↔ CA | Contract compatibility | PARTIAL | CA side tested; the dashboard's `core/server/caGateway.js` calls these same endpoints (read on the laptop) | The dashboard repository was not run or tested; its fixes from the earlier session are not committed or deployed |

---

## C. Integration results

- **Dashboard → CA:** the CA gateway's contract (`docs/dashboard-api.md`) is tested in the CA repository, including as the
  real limited database role. The dashboard's client code calls the same paths and sends the same fields (read, not run).
  The new fields (`workflow_status`, `error`, `execution`) and `GET /v1/campaigns` are additive: an older dashboard keeps
  working. **Not verified:** the dashboard running against the gateway.
- **CA → MA:** compatible, verified by the two-agent test with both real code bases over HTTP on one CI machine, and by
  level 6 against the real MA loader. **Real two-machine Tailscale communication was not verified.**

## D. End-to-end results

`tests/e2e/test_two_agents.py::test_both_agents_from_request_to_one_sent_email`, run by CI job "Contract with the real
Marketing Agent" (MA branch of the same name), which fails if any of its tests is skipped. Result: **passed: 3 run, 0 skipped, 0 failed** (with `test_real_ma.py`'s two tests; CA run 39021756275, against MA `d3e7c82`).

| # | Scenario step | Checked by |
|---|---|---|
| 1-2 | Campaign submitted, validated, stored | `interpret` request → `AWAITING_REVIEW`, version 1 |
| 3 | Proposal from the test research mechanism | CA stub model; level 6 by the real MA loader |
| 4 | Proposal returned to the dashboard interface | `workflow_status == PENDING_CAMPAIGN_APPROVAL` |
| 5-6 | Changes requested, proposal updated | `edit` → version 2, new sha256 |
| 7 | Correct version approved | approving version 1 → `stale_approval`; version 2 → live, `approved_by` recorded |
| 8-10 | Configuration generated, delivered, validated, kept | link 204 before approval, 401 without token; MA reports `(cid, 2)`; CA `applied == True`, `DELIVERED_TO_MA` |
| 11-14 | Campaign started, companies discovered, qualified, stored | one qualified lead tied to `[cid, 2]`; the three other fake results never became leads |
| 15-16 | Draft written and submitted for approval | draft `drafted`; the card shows campaign, draft, recipient, subject and body |
| 17 | Authorized reviewer approves the exact draft | `approved`, then `already_decided` on a repeat |
| 18-19 | Sent through the test provider, state stored | outbox: exactly one mail equal to the approved one; email `sent`, tied to `[cid, 2]` |
| 20 | States consistent | CA `DELIVERED_TO_MA`; one `MA_CAMPAIGN_APPLIED` event; after switch-off `CANCELLED`, and the MA stops ("no approved campaign is live") |

Negative cases:

| Case | Result | Evidence |
|---|---|---|
| Unapproved campaign cannot reach execution | PASS | Link 204 before approval (two-agent test); MA refuses a pass with no live campaign (MA `test_audit_fixes.py`) |
| Outdated approval rejected | PASS | Two-agent test; CA `test_publish.py` |
| Invalid configuration rejected | PASS | CA `test_real_ma.py` (forbidden key refused by the real MA); MA `test_campaign_link.py` (hash, keys, types) |
| Duplicate delivery does not duplicate execution | PASS | Two-agent test (pickup twice, MA rerun: same counts, nothing re-sent) |
| Disqualified company stays out of outreach | PASS | Two-agent test; MA `test_leadgen_pipeline.py`, `test_leadgen_qualify.py` |
| Rejected email never sent | PASS | MA `test_leadgen_safety.py::test_rejected_email_cannot_be_sent` |
| One draft's approval cannot authorize another | PASS | MA `test_leadgen_safety.py::test_an_approval_for_one_draft_cannot_authorize_another` (added) |
| Changed recipient or body invalidates the approval | PASS | MA `test_a_changed_body_after_approval_is_not_sent`, `test_a_changed_recipient_after_approval_is_not_sent`, Postgres triggers in `test_leadgen_postgres.py` |
| Duplicate Slack approval does not send twice | PASS | Two-agent test (`already_decided`, sends `[1, 0]`) |
| Unavailable MA does not lose an approved campaign | PASS | Two-agent test (approved before the MA ran); CA `test_acceptance.py` offline test |
| Temporary failure does not corrupt state | PASS | MA `test_an_error_mid_batch_releases_every_claim`; CA worker crash and lease tests (`test_worker.py`, `test_audit_fixes.py`) |
| Provider timeout does not cause a blind retry | PASS | MA `test_gmail.py::test_an_unclear_send_is_never_retried_blindly` and the three tests after it |

## E. Defects fixed during this work

| Defect (requirement) | Root cause | Fix | Regression test |
|---|---|---|---|
| A Gmail send could go out twice (MA-08) | `gmail.send_message` used `execute(num_retries=3)`, which re-sends on timeouts and 5xx; the sender then requeued any error up to 3 times | No client retry for the send; fixed Message-ID per email and recipient; unclear results held in `sending` and settled from the Sent folder; 4xx and 429 handled apart (`libs/agentkit/agentkit/gmail.py`, `agent/mailer.py`, `agent/leadgen/sender.py`, both repositories) | `agent/tests/test_gmail.py` (9 tests) |
| Slack card did not name the campaign or the exact draft (MA-07) | Not shown | Campaign id and version, draft id and content fingerprint on every card (`agent/leadgen/approval.py`, `models.py`, repositories) | `test_the_card_names_the_campaign_and_the_exact_draft` |
| No test that one draft's approval cannot authorize another (MA-07) | Untested | Test added | `test_an_approval_for_one_draft_cannot_authorize_another` |
| Slack decisions accepted a forged signature when `SLACK_SIGNING_SECRET` was missing, and a non-numeric timestamp passed the replay check (MA-07) | The secret was read with `!` (an unset secret became the text "undefined"); `Number("abc")` is `NaN`, and `NaN > 300` is false | Fail closed on both; the logic moved to `supabase/functions/slack-interact/handler.ts` | `handler_test.ts` (11 Deno tests, new CI step `deno test`) |
| An approval covered the draft at the moment of the click, not what the card showed (MA-06, MA-07) | The decision hashed the row as it was when clicked | The pending request stores the shown fingerprint; `decide_email()` returns `changed` when the draft differs (MA migration `0016_approval_shown_content.sql`, both repositories) | `test_a_draft_changed_after_its_card_was_posted_cannot_be_approved` (SQLite), `..._is_not_decided_in_postgres`, Deno `changed` test |
| Campaign contact roles were not applied (CA-05, MA-04) | Role priority was fixed in MA code | `contacts.preferred_roles` compiles to `preferred_roles` (closed vocabulary, CA `contract/model.py`); the MA checks it (`campaign_link.check_overlay`) and contacts those roles first without changing ranks or scoring (`agent/leadgen/roles.py`, `contacts.py`) | CA `test_compiler.py`, `test_real_ma.py` (real MA accepts it); MA `test_preferred_roles_come_first_and_the_rank_is_kept`, `test_the_link_accepts_known_preferred_roles_and_refuses_others` |
| No single workflow status, no execution progress, no list (CA-03) | Missing | `api/workflow.py`, `GET /v1/campaigns`, `execution` counts (`migrations/repeatable/ma_campaign_progress.sql`) | `test_workflow_status.py`, `test_dashboard_gateway.py`, two-agent test |
| No end-to-end test of both agents (all) | Missing | `tests/e2e/test_two_agents.py`, `tests/e2e/ma_driver.py`; CI job gets a database and fails on any skip | itself |
| The real-MA contract test was silently skipped in CI | The step's settings moved during an edit | Settings back on the pytest step; the job now fails on any skip | CI "Nothing skipped" step |
| CI red in both repositories | Fake keys joined by the formatter bot; an outdated test; type errors; formatting; a test with a duplicate provider id | Fixed one by one; both CI pipelines now report failures as annotations | CI |
| Audit findings F-02 to F-30 | See `docs/audit-2026-10-17.md` section N | Fixed earlier on this branch, verified by CI here | listed there |

### CI status of the verified commits

| Repository | Commit | Result |
|---|---|---|
| CA | `3004ced` | tests, type check, contract and two-agent job pass (3 run, 0 skipped); lint fixed by the format bot in the next commit; secret scan hit a scanner error |
| CA | `cbcd50b` (this report, on top of the format fix) | **all checks pass**, secret scan included (run 39023386475) |
| MA | `d3e7c82` | tests (with PostgreSQL), type check and security pass; one formatting line |
| MA | `829d66e` (that line fixed) | **all checks pass**: ci 39023367598, lint 39023367614, security 39023367499 |
| CA | `8b47795` (contact roles) | **all checks pass**, including the real MA accepting `preferred_roles` (run 39024096654) |
| MA | `ad7b216` (Slack endpoint tests, shown-card binding, contact roles) | **all checks pass**: ci 39024330247 (Python, PostgreSQL), lint 39024330209 (ruff, mypy, Deno format, lint and tests), security 39024330311 |

## Deployment on 2026-10-17 (after this report, at the owner's request)

| Step | Result |
|---|---|
| MA pipeline stopped (it was running a pass on the old code) | `desired_state` and `actual_state` = `stopped` |
| The pass's one open draft (Castles Technology) skipped | `decide_email(..., false)`: email `skipped`, lead `REJECTED`; its Slack card answers "Already decided" |
| All campaigns cleared (dashboard `campaign` rows and every CA table; MA leads and email history kept) | Before: 1 campaign, 10 requests, 2 versions, 1 state, 117 MA events; after: 0. Backup (CSV per table): `~/backups/campaigns-20261017-155623/` on the MA machine |
| MA migrations | `0014_approval_binding`, `0015_marketing_agent_role`, `0016_approval_shown_content` applied |
| MA code | `fix/audit-findings` deployed (`remote.sh deps`, `up`; `dnspython` installed); service running, pipeline stopped |
| CA migrations | `0025_version_insert_guard`, repeatable `ma_campaign_progress`, `ma_pipeline_grants` applied |
| CA code | `fix/audit-findings` deployed (`remote.sh deploy`; `pyjwt` installed); worker running, link listening on the Tailscale address |
| **Real two-machine link** (INT-06) | From the MA machine over Tailscale: `/ma/v1/health` → `ok`; `/campaign/active` → 204 (nothing live); without the token → 401 |

Not deployed: the `slack-interact` Edge Function (needs the Supabase CLI and project access; until redeployed, Slack shows
"Approved by ..." for a card that was in fact `expired` or `changed`, while nothing is decided), the dashboard and its
migration 007, and the gateway on Render (deploys from its own branch setting). The CA office window (`campaign_agent ui`)
still runs the code it started with until it is reopened.

## F. Remaining blockers

1. **Link settings on the MA machine** (`MA_LINK_URL`, `MA_LINK_TOKEN`) and `MA_LINK_ENABLED=true` on the CA (F-01).
2. **Migrations not applied** to the shared database: MA 0014, 0015, 0016; CA 0025 and the repeatable files; dashboard 007.
3. **Database logins** of the MA and dashboard backend still the owner (F-06) until switched to the new roles.
4. **No access** to: Slack (app, Edge Function deployment, reviewer list), Gmail account and sender domain, the two office
   machines, Tailscale ACLs, Render settings.
5. **Dashboard repository** belongs to another account; its changes are uncommitted and it has no running test here.
6. **Not implemented:** company-size targeting; independent web research of target companies; mailbox verification;
   keeping the text of earlier, never-decided draft versions.

## G. Production-readiness risks

- The deployed systems run older code: none of this branch is merged or deployed.
- Real Slack, Gmail, Chrome and Tailscale behaviour is unverified after these changes; run the soak suite (`qa/run.sh`)
  on the office machines with a test recipient before any real campaign.
- Gateway approvals name a verified person only after `API_REQUIRE_USER=true`.
- The MA's laptop `.env` points at production (F-11): keep separate dev and prod settings; production commands now need
  `MA_ON_OFFICE_MACHINE=1`.
- Targeting by company size is not enforced: Slack reviewers are the only filter for it.
- MA migration 0016 must be applied before the new MA code posts cards (it writes `shown_sha256`).

## H. Final verdict

1. **Does CA fulfil all of its responsibilities?** Not all. Reception, proposal, approval and editing, configuration and
   delivery are implemented and verified in CI. Research is partial: extraction plus optional suggestions, not
   evidence-backed research of companies.
2. **Does MA fulfil all of its responsibilities?** In code, mostly: reception, execution, qualification, persistence,
   drafting, the approval gate and safe sending are verified with test doubles. Real browser, Slack and Gmail operation
   are unverified, and draft versions are not kept as a history.
3. **Does the complete workflow function from submission to approved sending?** Yes, in an automated test of both real
   code bases on one machine (result above). It has not been shown on the two office machines with the real services.
4. **Still incomplete:** CA-02 (research), MA-06 (earlier draft texts are not kept), company-size targeting, mailbox
   verification.
5. **Unverified:** CA-06 and INT-06 over real Tailscale; MA-03 real Chrome; MA-07 real Slack; MA-08 real Gmail; INT-07 the
   dashboard; the production database grants.
6. **What prevents full completion:** the external services and the two machines are not reachable from here; the
   blockers in section F; the partial requirements above.
7. **Is it safe to run real campaigns and outbound email?** **No, not yet.** First: merge and deploy both branches,
   apply the migrations, set the link and the limited database logins, and pass the soak suite on the office machines
   with `TEST_RECIPIENT`. Then confirm SPF, DKIM and DMARC, and switch on `API_REQUIRE_USER`.
