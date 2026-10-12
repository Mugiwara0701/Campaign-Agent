# Store, Inbox and Worker (M6)

How the dashboard talks to the Campaign Agent (CA) through the database, and how the CA works through it.
This is the contract for the dashboard developer. Tables: [supabase-tables.md](supabase-tables.md); SQL:
`migrations/0013_campaign_agent_tables.sql`, `0014_campaign_agent_worker.sql` and `0015_campaign_agent_publish.sql` (drafts, not yet run on Supabase). Publishing is described in [publishing.md](publishing.md).

```text
dashboard server ──insert──▶ campaign_request ──claim──▶ worker (CA machine) ──▶ campaign_state, campaign_version
        ▲                                                                          │
        └──────────── polls campaign_state.review_view and the request row ◀───────┘
```

## Sending a request

Insert a row into `campaign_request`:

| Column | Meaning |
|---|---|
| `campaign_id` | the dashboard's `campaign.id` |
| `request_type` | `interpret`, `answer`, `edit`, `suggestion`, `discard`, `approve`, `rollback`, `deactivate` |
| `payload` | JSON, per type (below) |
| `base_revision` | the `revision` the user was looking at; **required for `edit`**, optional elsewhere |
| `requested_by` | the signed-in dashboard user (verified server side), at most 100 characters |
| `idempotency_key` | any string; a repeat for the same campaign is the same request, so retry freely |

Read `status` (`pending`, `processing`, `done`, `failed`), `error_code`, `error_detail` and `result` from the same row.
Requests of one campaign run strictly in order, one at a time.

| Type | Payload | Allowed when |
|---|---|---|
| `interpret` | `{}` or `{"message": "..."}`; without a message the campaign's `campaign_detail` is used | `DRAFT`, or `MODEL_UNAVAILABLE` (retry) |
| `answer` | `{"answers": [{"question_id": "q_..", "value": "c0"}]}`; `value` is a choice id, a list of ids, free text, or `"skip"` (`{"skip": true}` also works) | `CLARIFICATION_REQUIRED` |
| `edit` | `{"text": "..."}` (in words) or `{"ops": [{"path": "qualification.min_qualify", "value": 55}]}` (a direct change; `null` clears; optional `"op": "add"/"remove"` for lists) | `AWAITING_REVIEW`, `VALIDATION_FAILED`, `CLARIFICATION_REQUIRED`; direct ops also `MODEL_UNAVAILABLE` |
| `suggestion` | `{"suggestion_id": "s_..", "decision": "accept"}` or `"reject"` | review states |
| `discard` | `{}` | any state except `PUBLISHED` and `DISCARDED` |
| `approve` | `{"version": 3, "sha256": "..."}`: the version and text the user reviewed | `AWAITING_REVIEW`, or `PUBLISH_FAILED` (retry) |
| `rollback` | `{"version": 1}`: a version that was approved before | any, once a version was approved |
| `deactivate` | `{}` | any, once a version was approved |

Answers and direct edits are all-or-nothing: if one is refused, none is applied.

## What the agent writes back

`campaign_state` has one row per campaign. The dashboard reads **`current_state`**, **`revision`** and **`review_view`**
(a JSON document: fields with where each value came from, open questions, web suggestions, what could not be applied,
notices, warnings, the YAML preview with validation results per level, and a fixed statement that every email is
approved in Slack). The working copy of the campaign (`ir`) and the conversation internals are not readable by the
dashboard role. While a request runs, `current_state` shows `INTERPRETING`.

`campaign_version` holds the stored versions (YAML, sha256, validation report, not-applied list, a snapshot of the
campaign). A version with `approved_at` set was approved; the one pointed to by `campaign_state.active_version_id` is
live. The dashboard sees which in `review_view` (`live`, `live_version`, `versions`).

`ca_heartbeat` is a single row updated every cycle. If `last_seen` is old, the CA machine is down and requests wait.

## Failures

A refused request has `status = failed` and one of these codes; it never changes the campaign.

| `error_code` | Meaning |
|---|---|
| `invalid_request` | malformed payload, unknown type, or ops the contract refuses (details in `result.rejected`) |
| `stale_revision` | `base_revision` is not current; `result.revision` has the current one. Reload and retry |
| `missing_base_revision` | an `edit` without `base_revision` |
| `wrong_state` | the request is not allowed in the current state |
| `input_rejected` | the text was empty, too long, not English, or looked like a secret |
| `needs_rephrase` | the model's reply could not be read, even after one repair |
| `model_unavailable` | the model did not answer; the draft is kept and the state is `MODEL_UNAVAILABLE`. Send `interpret` again |
| `invalid_answer` | an answer was refused; `result.errors` lists which and why |
| `not_available` | `start_pipeline` on an approval: the Start button is the only way for now |
| `stale_approval` | the approved version is not the latest candidate, or its text changed; `result.current` has the one to review |
| `publish_blocked` | the candidate no longer passes the checks when approved; the campaign is `VALIDATION_FAILED`; `result.issues` says why |
| `publish_failed` | the store did not accept the change; nothing was applied and the same approval can be sent again |
| `internal_error` / `worker_crashed` | a bug or a repeatedly crashing worker, after all attempts |

## How the worker behaves

- `python -m campaign_agent start` polls every 2 seconds (setting `POLL_INTERVAL_S`), beats the heartbeat, claims the
  oldest request that may run (`FOR UPDATE SKIP LOCKED`, nothing older and nothing running for the same campaign) and
  runs it. `once` takes at most one request and exits.
- A request runs in three short transactions with **no model call inside any of them**: claim; load and show
  "working"; then store the new state, the candidate version and the result together.
- Nothing is stored until the end, so a request that dies half-way changes nothing. Its lease (`LEASE_S`, default 30
  minutes) runs out and it is tried again, up to `MAX_ATTEMPTS` (3), then it fails with `worker_crashed`.
  An unexpected error in the code releases the request for another try the same way (`internal_error` at the end).
- The saved conversation is the truth about the state. `current_state` is only a display value and is never read back.
- The connection works behind Supabase's transaction pooler (no prepared statements).
- Logs are JSON, one per line, with request and campaign ids only: no user text, prompts or secrets.

## Roles

| Role | Can | Cannot |
|---|---|---|
| `campaign_dashboard` (optional; the dashboard may keep using its service key) | insert requests; read request status, `review_view`, versions, heartbeat, `campaign` | read the working IR or conversation, update or delete anything, insert a request that is already `done`, touch any other table |
| `campaign_agent` | read `campaign`; work the inbox; write state, candidate versions, heartbeat | change a stored version's content, approve twice, change the dashboard's `campaign` table, read or write the Marketing Agent's tables, delete or drop anything |

Both are checked by tests that run as the real roles against a real PostgreSQL.

## Decisions and limits

- Candidates are frozen when a campaign reaches review (M6); approval, going live, going back and stopping are in M7.
- The migrations are **not applied to Supabase**. They were applied and tested on a throwaway PostgreSQL 16.
  Applying them to the shared database should be done by the owner, in order, after review.
- The local test database is PostgreSQL, not SQLite: the claim query, roles and triggers are PostgreSQL features.
- Research (M9) is not run yet: `research_needs` from the model are ignored.
