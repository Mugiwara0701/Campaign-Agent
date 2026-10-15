# Dashboard integration guide (final)

For the dashboard developer. **Read this first.** It says what to build, how, and what not to do. The detail behind each
point is in the files listed at the end.

## The one idea

The **Campaign Agent (CA)** is the brain. It reads the campaign the user types, asks questions, writes the leadgen file,
and, once a person approves it, runs the **Marketing Agent (MA)** on it. The dashboard does **not** talk to the CA or the
MA over the network, and it does **not** control the MA. It does only two things, both with the shared Supabase database:

1. **Write requests** ("here is the campaign", "here are my answers", "approve this", "pause").
2. **Read the answer** (what the CA wants to show, and what state things are in).

```text
 user ── dashboard ──insert──▶  Supabase  ◀──poll── CA service (office) ──asks──▶ MA service (office)
          (your code)   ◀──read──  (tables)  ──write──▶                      (the CA starts and stops it)
```

No backend of ours is needed, and nothing is hosted on Render for this.

## Two ways to reach the CA

1. **Straight to the database** (what the sections below show in SQL): your server inserts the rows and reads the tables.
2. **Through the gateway**, a small web service on Render that this repository provides: your server makes plain HTTPS calls
   and the gateway does the database work. See [dashboard-api.md](dashboard-api.md). The requests and states are identical;
   only the way of sending them differs. The CA itself is private and reached through neither.

## What you do NOT build any more

The MA's own Start and Stop (the Render "pipeline API", `/api/v1/pipeline/*`, the `request_pipeline()` function and the
`pipeline_control` table) are **not the dashboard's job now**. The CA is the only thing that starts or restarts the MA.

- **Remove or hide the MA Start / Stop buttons.** Two controllers would contradict each other (the dashboard says
  stopped, the CA says running).
- Do not call the pipeline API, and do not write `pipeline_control`.
- You will not build a "pipeline controller" for the MA. What you build is below.

## What you build (checklist)

| # | Screen / function | Section |
|---|---|---|
| 1 | Create a campaign (the user types it) | 1 |
| 2 | The campaign review and approval page | 2 |
| 3 | Pause and resume the CA | 3 |
| 4 | Show what the MA is doing with the live campaign (read only) | 4 |
| 5 | Error handling | 5 |
| 6 | Who is signed in (security) | 6 |

## 0. Before you start

1. The database migrations `0013` to `0019` are applied (`0020` pause also stops the MA, `0021` auto-start; see below) to the shared database (they are). The optional sign-in file is `migrations/optional/dashboard_auth.sql`, see 6.
2. The CA service is running on the office machine. Check: `select last_seen, agent_status from public.ca_heartbeat;` The
   `last_seen` must be within the last minute. If it is older, show **"the assistant is offline"**, not a spinner.
   `agent_status` is `idle`, `busy` or `paused`.
3. Database access: either your server's service key, or the limited role `campaign_dashboard` (it can insert requests and
   read the CA's tables and nothing else). Do not use the database owner's login.

## 1. Create a campaign

The user types the campaign. Two inserts, in this order:

```sql
-- the campaign row (your existing table; this is what you do today)
insert into public.campaign (campaign_name, campaign_detail, start_date, end_date, is_active) values (...) returning id;

-- the request that makes the CA start working on it
insert into public.campaign_request (campaign_id, request_type, payload, requested_by, idempotency_key)
values ($id, 'interpret', '{"message": "<the text the user typed>"}', $signed_in_user, $unique_key)
on conflict (campaign_id, idempotency_key) do nothing;
```

- **The CA starts because of the `interpret` request.** Send it yourself right after saving the campaign: it starts
  immediately. If you do not (or it is late), the CA service also notices a **new campaign that has no request at all** and
  sends that one request by itself (checked every 10 seconds; a campaign must be at least 20 seconds old, so yours wins
  if you send it; it must have text; it must be less than 7 days old). It uses `campaign_detail` as the message and
  records the sender as `campaign-agent (auto-start)`. This needs migration `0021`. The CA ignores `is_active` and the dates.
  A campaign that already has any request, or has been touched, is never started this way, so a discarded campaign stays
  discarded.
- Then poll the request row until `status` is `done` or `failed` (every 1 to 2 seconds; a model call can take minutes on the
  office machine, so show the state), and read the screen as in section 2.

## 2. The review and approval page

Everything on this page comes from one JSON document, `campaign_state.review_view`. Do not keep your own copy of the
campaign. Read it again after every request.

```sql
select current_state, revision, review_view from public.campaign_state where campaign_id = $1;
select status, error_code, error_detail, result from public.campaign_request where id = $1;
```

### What to show for each `current_state`

| State | Screen | The user can |
|---|---|---|
| `DRAFT` | the form | send the message (`interpret`) |
| `INTERPRETING`, `RESEARCHING`, `VALIDATING` | "working" | wait |
| `CLARIFICATION_REQUIRED` | the questions (`review_view.questions`) | `answer` (or `edit`, `discard`) |
| `MODEL_UNAVAILABLE` | "the language model did not answer" | `interpret` again |
| `AWAITING_REVIEW` | **the review** (below) | `approve`, `edit`, `suggestion`, `discard` |
| `VALIDATION_FAILED` | the failed checks, with the suggested fix | `edit` |
| `PUBLISHED` | live version, who approved, whether the MA applied it | `edit` (new version), `rollback`, `deactivate` |
| `PUBLISH_FAILED` | "could not publish, try again" | `approve` again |
| `DISCARDED` | closed | nothing |

### The review (`AWAITING_REVIEW`)

Show, in this order:

1. **Fields by section** (`sections[].fields[]`): the label and value, a badge for `status` (`confirmed`, `inferred`,
   `defaulted`), the quote it came from (`evidence_quote`), and a tag "**not applied yet**" when `mapping` is `proposed` or
   `not_emitted`.
2. **"Could not be applied"** (`not_applied[]`: `request`, `reason`, `proposal`). **Always show it, prominently.** The MA
   cannot do some things yet (for example a country filter or company size). Hiding this list is not allowed.
3. **Notices and warnings** (`notices[]`, `warnings[]`).
4. **Web suggestions** (`suggestions[]`), each with Accept and Reject, only if present.
5. **The leadgen file**: `candidate.yaml`, read only, with its checks `candidate.levels[]`: L3 schema, L4 business rules,
   L5 YAML, L6 the MA's own loader. Each is `passed`, `failed` or `skipped`; show the `issues`. If L6 is `skipped`, show its
   `note`, because it means the file was not checked by the MA's own loader.
6. **The email sentence** (`email_approval`), exactly as given. Every email is approved by a person in Slack. Approving a
   campaign sends nothing by itself.
7. **Actions**
   - **Approve**: enabled only when `candidate.can_freeze` is true and no level `failed`.
   - **Request a change**: a text box ("also include Austria"); sends `edit`.
   - **Reject**: there is no "reject" request. Reject the whole thing with `discard` (ask for confirmation first).

### Every request you send (`campaign_request`)

| Column | Value |
|---|---|
| `campaign_id` | the campaign |
| `request_type` | `interpret`, `answer`, `edit`, `suggestion`, `discard`, `approve`, `rollback`, `deactivate` |
| `payload` | per type, below |
| `base_revision` | the `revision` the user was looking at. **Required for `edit`.** |
| `requested_by` | the signed-in user's email, at most 100 characters. **It is the approval record.** |
| `idempotency_key` | any string; a repeat for the same campaign is the same request, so retries and double clicks are safe |

| Type | Payload | Allowed in |
|---|---|---|
| `interpret` | `{"message": "..."}` | `DRAFT`, `MODEL_UNAVAILABLE` |
| `answer` | `{"answers": [{"question_id": "q_..", "value": "c0"}]}` (a choice id, a list of ids, free text, or `"skip"`) | `CLARIFICATION_REQUIRED` |
| `edit` | `{"text": "..."}` or `{"ops": [{"path": "qualification.min_qualify", "value": 55}]}` | `AWAITING_REVIEW`, `VALIDATION_FAILED`, `CLARIFICATION_REQUIRED` |
| `suggestion` | `{"suggestion_id": "s_..", "decision": "accept"}` or `"reject"` | review states |
| `discard` | `{}` | any state except `PUBLISHED`, `DISCARDED` |
| `approve` | `{"version": N, "sha256": "...", "start_pipeline": true/false}` | `AWAITING_REVIEW`, `PUBLISH_FAILED` |
| `rollback` | `{"version": N}` (a version that was approved before) | once something was approved |
| `deactivate` | `{}` | once something was approved |

### Approving, exactly

- Send `approve` with the **`version` and `sha256` the user was looking at**, copied from `candidate`. If the file changed
  in the meantime the request fails with `stale_approval` and `result.current` has the new one: show it again. Never send an
  approval for something the user did not see.
- Use a stable key, for example `approve-<version>-<first 12 of sha256>`, so a double click is one approval.
- **`start_pipeline`** decides whether approving also **runs the MA**. `true`: after the publish is saved, the CA starts the
  MA on the new campaign (if the MA was already running, the CA stops it and starts it again so it takes the new file).
  `false` or missing: it only publishes; nothing runs. The default product behaviour we want is **`true`**: approval
  means "run this campaign". Read `result.pipeline` on the request afterwards: `asked`, `restarted`, and `reason` if it
  could not (the campaign is live either way).
- Only one campaign is live at a time. Approving another one replaces it (the old one keeps its versions and can be brought
  back with `rollback`).
- After approving, poll the request to `done`, then read `review_view` again: `live`, `live_version`, `versions[]`.

## 3. Pause and resume the CA

One function and one row. No server needed.

```sql
select * from public.ca_request_state('paused',  'user@example.com');   -- pause
select * from public.ca_request_state('running', 'user@example.com');   -- resume
select desired_state, actual_state, requested_by, requested_at, state_since from public.ca_control;
```

With the Supabase client: `supabase.rpc('ca_request_state', { p_state: 'paused', p_by: userEmail })`.

- Show **both** `desired_state` and `actual_state`. They differ for a few seconds after a click ("pausing...", "resuming...").
- **Paused** means the CA takes **no new request**. A request already running **finishes**. New requests wait as `pending`
  and run, in order, after resume. Nothing is lost. A paused CA stays paused after a restart.
- **Pausing the CA also pauses the MA.** The CA asks the MA's pipeline to stop (the pass in progress is cancelled and its
  Chrome closes), and while the CA stays paused it keeps the MA stopped (it checks every 30 seconds, so a Start pressed by
  hand is undone). **Resuming the CA starts the MA again only if it was running when the pause came**; a pipeline that was
  already stopped stays stopped. This needs migration `0020` (one column that remembers it). Without it, pause only pauses
  the CA.

## 4. Show what the MA is doing (read only)

The MA reports to the CA, and the CA puts it on the same screen data. Read it from `review_view`; do not look for the MA
anywhere else.

- `live`, `live_version`: this campaign is the live one.
- `applied`: `true` the MA runs it; `false` not yet; `null` not known (the MA is offline; it applies it when it comes back).
- `ma_error`: why the MA refused the file, if it did.
- `ma_events[]`: **open blockers** (`kind: "blocker"`) and the last applied/rejected events. Show each open blocker with its
  `message` and `severity` as an alert: it means something needs a person (for example the MA's Gmail login expired, or its
  browser did not start). A blocker disappears by itself when the MA reports it fixed.
- `ca_heartbeat` and `ca_control`: the CA itself (online, busy, paused).

## 5. Errors

A refused request has `status = failed`, an `error_code` and a short `error_detail` meant for the user. It never changes the
campaign. Main ones:

| `error_code` | What to do |
|---|---|
| `stale_approval` | the file changed; show the new one (`result.current`) and ask again |
| `stale_revision` | reload, then let the user retry (`result.revision` is the current one) |
| `missing_base_revision` | you sent `edit` without `base_revision` |
| `wrong_state` | the action is not allowed in this state; reload |
| `invalid_answer` | an answer was refused; `result.errors` says which |
| `input_rejected` | the text was empty, too long, not English, or looked like a secret |
| `publish_blocked` | the checks no longer pass, or the MA cannot load this file version; show `result.issues` |
| `publish_failed` | nothing was applied; the same approval can be sent again |
| `model_unavailable` | send `interpret` again |
| `rate_limited` | more than 30 requests from one user in a minute; wait and retry |

The full list is in `store-and-inbox.md`.

## 6. Who is signed in (security)

`requested_by` is the record of **who approved a leadgen file**. It must be the real signed-in user.

- **If your server inserts the requests** (it checks the login, then writes `requested_by` itself): that is correct and
  needs nothing more.
- **If the browser writes to the database directly with Supabase Auth**: the browser would supply the name and anyone could
  claim anyone. Use `migrations/optional/dashboard_auth.sql` (optional, applied by hand): the database then accepts a request only when
  `requested_by` equals the signed-in email **and** that email is on an allow-list (`ca_dashboard_user`). First **turn off
  public sign-ups** in Supabase Auth. For pause and resume it adds `ca_request_state_signed_in(p_state)`.
- Never put the database owner's login or the `service_role` key in the browser.

## What not to do (summary)

- Do **not** build, show or call a Start/Stop for the MA, and do not use the pipeline API or write `pipeline_control`.
- Do **not** send an approval for a version the user did not see; always send the `version` and `sha256` from `candidate`.
- Do **not** hide `not_applied`, a failed check, a skipped L6, or an open MA blocker.
- Do **not** keep your own copy of the campaign; render `review_view` and re-read it after each request.
- Do **not** write `campaign_state`, `campaign_version`, `ca_control` or `ma_event` yourself. You write only
  `campaign` and `campaign_request`, and call `ca_request_state`.
- Do **not** take `requested_by` from the page when the browser writes directly (use the optional sign-in file).
- Do **not** show an endless spinner when the CA is offline; check `ca_heartbeat`.
- Do **not** show or promise that approving sends emails. Every email still needs a person's approval in Slack, and the MA
  sends only to the test address while test mode is on.
- Do **not** call the CA or the MA over HTTP. There is no API to call.

## Not built yet (decisions needed)

- **Stop the MA on its own, without pausing the CA.** Pausing the CA stops the MA, but there is no separate "stop only the
  MA" button. Say if you need one (it would be a request handled by the CA, so the CA stays the only controller).
- **"Is the MA running right now"** is not readable by the dashboard role (only what it did for the live campaign is, in
  `review_view`). A small read-only grant would add it. Say if you need this.
- **The campaign's own on/off switch and dates** do not gate the MA unless the owner sets `MA_LINK_RESPECT_SCHEDULE=true`.
- Several campaigns live at once are not supported (one at a time).

## Trying it without the dashboard

- `bash deploy/remote.sh try "Find ..."` plays the dashboard's part from a terminal (`docs/testing-without-the-dashboard.md`).
- The window on the CA machine (`python -m campaign_agent ui`) does the same with buttons and is a working example of the
  whole loop: type, answer, review, approve, start the MA. Its code, `src/campaign_agent/console_page.py`, shows the exact
  requests.

## Files

| File | For |
|---|---|
| `docs/dashboard-integration.md` (this) | what to do and not do |
| `docs/handover.md` | the short loop and the state table |
| `docs/store-and-inbox.md` | every request type, payload and error code |
| `docs/publishing.md` | approval, live, rollback, `start_pipeline` |
| `docs/dashboard-api.md` | the optional HTTPS gateway on Render |
| `docs/dashboard-pause-resume.md` | pause and resume in detail |
| `docs/supabase-tables.md` | the tables and who writes each |
| `src/campaign_agent/view.py` | the exact shape of `review_view` |
| `migrations/0013`..`0020` (`optional/dashboard_auth.sql` optional) | what is in the database |
