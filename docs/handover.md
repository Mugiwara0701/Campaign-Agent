# Handover: connecting the dashboard to the Campaign Agent

> **Start with [dashboard-integration.md](dashboard-integration.md)**: the final guide (what to build, how, and what not to do).
> The dashboard no longer controls the Marketing Agent's pipeline: the Campaign Agent runs it. This page is the detail.

For the dashboard developer. The Campaign Agent (CA) has **no HTTP API**. You talk to it through three tables in the
shared Supabase database: you insert **requests**, and you read the **state** and the **result**. This page is the short
version; the full contract is [store-and-inbox.md](store-and-inbox.md) (requests, errors, roles) and
[publishing.md](publishing.md) (approval, live, rollback).

## What you need before you start

1. The migrations in `migrations/` applied to the shared database by its owner, in order (`bash deploy/remote.sh migrate
   --plan` shows what is missing). `0013` to `0019` are applied; `0020` (the Marketing Agent pauses with the Campaign Agent) is next. `migrations/optional/dashboard_auth.sql` is optional and is not applied by the runner (see "Who is signed in" below).
2. A database login for the dashboard server. Either keep using the service key, or use the limited role
   `campaign_dashboard` (created by `0014`): it can insert requests, and read request status, `review_view`, versions,
   the heartbeat and the Marketing Agent's reported status; nothing else.
3. The Campaign Agent service running on the office machine ([runbook.md](runbook.md)). Check it is alive:
   `select last_seen from public.ca_heartbeat;` should be within the last minute. If not, requests wait; show the user
   "the assistant is offline" rather than an endless spinner.

You can try every call below without the dashboard: `bash deploy/remote.sh try "Find ..."` plays the dashboard's part
([testing-without-the-dashboard.md](testing-without-the-dashboard.md)).

## The loop

```text
user types the campaign  ->  insert request 'interpret'  ->  poll the request row until status is done or failed
                              then read campaign_state.review_view and show it
user answers a question  ->  insert 'answer'             ->  poll, read review_view again
user edits               ->  insert 'edit' (with base_revision)
user approves            ->  insert 'approve' {version, sha256 of what they reviewed}
```

Insert (the server fills `requested_by` with the signed-in user, at most 100 characters):

```sql
insert into public.campaign_request (campaign_id, request_type, payload, base_revision, requested_by, idempotency_key)
values ($1, 'interpret', '{"message": "Find robotics companies building warehouse robots on Linux"}', null, $2, $3)
on conflict (campaign_id, idempotency_key) do nothing;
```

Poll the request and read the screen:

```sql
select status, error_code, error_detail, result from public.campaign_request where id = $1;
select current_state, revision, review_view from public.campaign_state where campaign_id = $1;
```

- Poll every 1 to 2 seconds while a request is `pending` or `processing`. On a CPU-only machine a request can take
  minutes; show the `current_state` (`INTERPRETING` while work runs).
- `idempotency_key` makes retries safe: a repeat for the same campaign is the same request.
- Requests of one campaign run in order, one at a time. A request that arrives while another runs just waits.
- `edit` must carry `base_revision` (the `revision` the user was looking at). A stale one fails with `stale_revision` and
  the current revision in `result.revision`: reload and let the user retry.

## What each state means for the screen

| `current_state` | Show | The user can |
|---|---|---|
| `DRAFT` | the form | send the message (`interpret`) |
| `INTERPRETING`, `RESEARCHING`, `VALIDATING` | "working" | wait |
| `CLARIFICATION_REQUIRED` | the questions in `review_view.questions` | `answer`, `edit`, `discard` |
| `MODEL_UNAVAILABLE` | "the language model did not answer" | `interpret` again; direct field edits still work |
| `AWAITING_REVIEW` | the review: fields and where each came from, "could not be applied", suggestions, the YAML and its checks | `edit`, `suggestion`, `approve`, `discard` |
| `VALIDATION_FAILED` | the failed checks in `review_view.candidate.levels` | `edit` (a fix is offered where one is obvious) |
| `PUBLISHED` | the live version and whether the Marketing Agent applied it | `edit` (starts version N+1), `rollback`, `deactivate` |
| `PUBLISH_FAILED` | "could not publish, try again" | `approve` again (safe) |
| `DISCARDED` | closed | nothing |

Rest states are what `current_state` shows; the conversation itself is stored by the agent.

## What to show from `review_view`

`review_view` is one JSON document; its shape is the `ReviewView` model in `src/campaign_agent/view.py`.

- `sections[].fields[]`: label, value, **status** (`confirmed`, `inferred`, `defaulted`), the quote it came from, and
  `mapping` (`supported`, or `proposed` / `not_emitted` = shown but **not applied yet**).
- `questions[]`: id, text, choices, whether it can be skipped (`answer` payload: `{"answers": [{"question_id": ..,
  "value": ..}]}`).
- `suggestions[]`: pending web suggestions with source links (`suggestion` accept or reject). Only present when research
  is switched on.
- `not_applied[]`: what the user asked for that the Marketing Agent cannot do yet (US-only, company size, preferred
  roles, LinkedIn, ...). **Always show it.** Silent dropping is not allowed.
- `candidate`: `version`, `sha256`, the `yaml`, `can_freeze`, and `levels[]` (L3 schema, L4 business rules, L5 YAML,
  L6 the Marketing Agent's own loader) with issues. L6 `skipped` means the loader could not be run; read the note.
- `email_approval`: a fixed sentence; show it. **Every email is still approved by a person in Slack**; approving a
  campaign sends nothing and starts nothing.
- After publishing: `live`, `live_version`, `versions[]` (who approved and when), and
  **`applied`**: `true` (the Marketing Agent runs it), `false` (not yet), or `null` (it has not reported, for example it
  is offline; the campaign is applied when it returns). `ma_error` says why the Marketing Agent refused it, if it did.

## Approving

Send `approve` with the **version and sha256 the user reviewed**, exactly as shown. If the candidate changed in the
meantime you get `stale_approval` with the current one in `result.current`: show it again. Approving what is already live
is harmless. At most one campaign is live at a time; approving another replaces it (it keeps its versions and can be
brought back with `rollback`).

## Errors

Every failed request has `error_code` and a short `error_detail` meant for the user. The codes and what to do are in
[store-and-inbox.md](store-and-inbox.md). Two were added late: **`rate_limited`** (more than 30 requests from one user in
a minute; wait and retry) and **`publish_blocked`** also when the Marketing Agent reported that it cannot load our contract
version (`result.ma_supported` lists what it can).

## Starting the Marketing Agent from an approval

Add `"start_pipeline": true` to the `approve` payload and the Campaign Agent also asks for the Marketing Agent's pipeline to
run, once the publish is committed. Without it (or `false`) nothing is started, as before. Read `result.pipeline` on the
approve request: `asked` (it was asked), `restarted` (it was already running, so it was stopped and started again to take
the new file), and `reason` when it could not (`not_allowed`, `pipeline_not_readable`). The campaign is live in every case.

## A new campaign starts by itself

The Campaign Agent also starts on a campaign that was saved without an `interpret` request (migration 0021; the service
checks every 10 seconds; the campaign must have text, be older than 20 seconds and younger than 7 days, and have no
request and no state). Sending the request yourself right after saving is still the fast, normal way.

## Pause and resume the Campaign Agent

See [dashboard-pause-resume.md](dashboard-pause-resume.md): one function, one row, no server.

## Who is signed in (only if the browser writes to the database directly)

`requested_by` is the approval record. A dashboard server fills it from its own login, which is safe. If instead the page
talks to the database directly with Supabase Auth, use `migrations/optional/dashboard_auth.sql` (optional; apply it by hand, the migration runner skips that folder): the
database then accepts a request only when `requested_by` is the signed-in person's own email and that email is on an
allow-list (`ca_dashboard_user`). Turn off public sign-ups in Supabase Auth first. For pause and resume it adds
`ca_request_state_signed_in(p_state)`, which uses the signed-in email.

## What is not available yet
- Campaign fields the Marketing Agent does not support (US-only, company size, preferred roles): the agent stores them,
  reports them under `not_applied`, and does not emit them.
- Several campaigns live at once.
- The dashboard's `is_active` and start/end dates do not gate the Marketing Agent unless `MA_LINK_RESPECT_SCHEDULE=true`.
- Whether the Marketing Agent applies a published version needs its side of M8
  ([marketing-agent-m8-work.md](marketing-agent-m8-work.md)); until then `applied` stays `null` and level 6 is `skipped`.

## Who to ask

The design is in [campaign-setup-agent-architecture.md](campaign-setup-agent-architecture.md); operations are in
[runbook.md](runbook.md); security and data retention in [security-review.md](security-review.md) and
[retention.md](retention.md).
