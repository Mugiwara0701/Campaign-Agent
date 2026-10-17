# Dashboard developer guide: what to add in the backend and the frontend

This is the one document to read. It says what to build, how, and what not to do. The Campaign Agent (CA) is already deployed
and reachable; you only add the calls to it.

## 0. What you are given

| Item | Value |
|---|---|
| Gateway address | `https://campaign-agent-maha.onrender.com` |
| Gateway token | **sent to you separately by the owner. It is not in this repository and must never be.** |
| What it is | a small HTTPS service in front of the CA. Your **server** calls it; nothing in the browser does. |

Add two settings to the dashboard's **server** environment (not to browser code, not to Git):

```text
CA_GATEWAY_URL=https://campaign-agent-maha.onrender.com
CA_GATEWAY_TOKEN=<the token you were given>
```

Check it from a terminal before writing any code:

```bash
curl https://campaign-agent-maha.onrender.com/health
# {"status":"ok"}

curl -H "Authorization: Bearer $CA_GATEWAY_TOKEN" https://campaign-agent-maha.onrender.com/v1/ca
# {"online":true,"status":"idle","last_seen_s_ago":1.2,"control":{"desired_state":"running","actual_state":"running",...}}
```

Without the token the second call returns `401`. The gateway is on a free plan: after about 15 minutes without a call it sleeps and
the next call takes 30 to 60 seconds. Give your server calls a timeout of about 90 seconds.

## 1. How it fits together

```text
 browser ──▶ dashboard frontend ──▶ dashboard SERVER ──(HTTPS + token)──▶ gateway on Render ──▶ Supabase ◀── CA (private office machine)
                                     your new routes                                                          │
                                                                                                              └─▶ Marketing Agent (MA)
```

- The **CA is the brain.** It reads the campaign, asks questions, writes the leadgen file, and when a person approves it, runs
  the **Marketing Agent (MA)** on it. The MA and the CA live in the office and are private; the dashboard never reaches them.
- The dashboard only does two things: **send a request** ("here is the campaign", "here are the answers", "approve") and **read
  what to show**. Everything is asynchronous: a request is recorded at once, and the CA works on it (a model call takes a few
  minutes). You poll for the result.
- The campaign **starts by itself** even if you send nothing: the CA notices a new campaign within about 30 seconds. Sending
  the `interpret` request yourself starts it instantly, so do that.
- **A person approves every campaign** in the dashboard, and every email is approved by a person in Slack. Nothing here changes that.
- The dashboard **does not control the MA**. Remove the MA Start and Stop buttons and routes (section 5).

## 2. Backend changes (the dashboard's server code)

### 2.1 A small helper that calls the gateway

The examples are TypeScript for a Next.js app; adapt them to your stack. The token is read on the server only.

```ts
// lib/caGateway.ts  (server only)
const BASE = process.env.CA_GATEWAY_URL!;
const TOKEN = process.env.CA_GATEWAY_TOKEN!;

export class GatewayError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function gateway<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: init.method ?? "GET",
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
    signal: AbortSignal.timeout(90_000),   // the first call after a sleep takes up to a minute
  });
  if (!res.ok) throw new GatewayError(res.status, await res.text());
  return res.json() as Promise<T>;
}
```

### 2.2 After saving a campaign, start the CA on it

In the route that already saves the campaign, add one call after the insert:

```ts
const campaign = await saveCampaign(form);                 // what you do today
await gateway(`/v1/campaigns/${campaign.id}/requests`, {
  method: "POST",
  body: {
    request_type: "interpret",
    payload: {},                                           // empty: the CA uses the campaign's own text
    requested_by: user.email,                              // the signed-in user, from YOUR session
    idempotency_key: `interpret-${campaign.id}`,
  },
});
```

If this call fails, do not fail the save: the CA still starts on the campaign by itself.

### 2.3 Routes the frontend will call

The frontend never talks to the gateway. Add these routes to your server; each one forwards to the gateway. **`requested_by`
is always filled by your server from the signed-in user. Never take it from the request body.**

| Your route | Forwards to the gateway | Notes |
|---|---|---|
| `GET /api/campaigns/:id` | `GET /v1/campaigns/:id` | the whole screen: state, revision, `review_view`, newest request |
| `POST /api/campaigns/:id/requests` | `POST /v1/campaigns/:id/requests` | send `interpret` (a retry), `answer`, `edit`, `suggestion`, `approve`, `discard`, `rollback`, `deactivate`; allow only these types |
| `GET /api/requests/:id` | `GET /v1/requests/:id` | optional: one request's status |
| `GET /api/ca` | `GET /v1/ca` | is the CA online, is it paused |
| `POST /api/ca/state` | `POST /v1/ca/state` | `{ "state": "paused" or "running" }`; pause also stops the MA |

```ts
// app/api/campaigns/[id]/requests/route.ts
import { NextResponse } from "next/server";
import { gateway, GatewayError } from "@/lib/caGateway";
import { getSessionUser } from "@/lib/session";            // your existing login check

const ALLOWED = new Set(["interpret", "answer", "edit", "suggestion", "approve", "discard", "rollback", "deactivate"]);

export async function POST(req: Request, { params }: { params: { id: string } }) {
  const user = await getSessionUser(req);
  if (!user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const body = await req.json();
  if (!ALLOWED.has(body.request_type)) return NextResponse.json({ error: "bad request_type" }, { status: 422 });
  try {
    const sent = await gateway(`/v1/campaigns/${params.id}/requests`, {
      method: "POST",
      body: {
        request_type: body.request_type,
        payload: body.payload ?? {},
        base_revision: body.base_revision ?? null,
        idempotency_key: body.idempotency_key,             // the frontend sends a stable key (section 3.4)
        requested_by: user.email,                          // never from the body
      },
    });
    return NextResponse.json(sent, { status: 202 });
  } catch (e) {
    const status = e instanceof GatewayError ? e.status : 502;
    return NextResponse.json({ error: "assistant request failed" }, { status });
  }
}
```

The other four routes are the same shape: check the user, call `gateway(...)`, return the JSON. For `POST /api/ca/state` also
send `requested_by: user.email`. Limit who may pause the CA to the people who should be able to.

### 2.4 Remove the MA controls

Delete the routes and buttons that start or stop the MA (the pipeline API, `request_pipeline`, `pipeline_control`). The CA runs
the MA now, and two controllers would contradict each other.

## 3. Frontend changes

### 3.1 The data you work with

One call gives the whole screen: `GET /api/campaigns/:id`.

```ts
type CampaignView = {
  campaign_id: string;
  state: string;               // NEW before the CA has touched it, then the states in 3.2
  revision: number | null;     // send it back as base_revision with an edit
  review_view: ReviewView | null;
  last_request: { id: string; request_type: string; status: "pending" | "processing" | "done" | "failed";
                  error_code: string | null; error_detail: string | null; result: any } | null;
};

type ReviewView = {
  state: string; revision: number;
  sections: { name: string; title: string; fields: {
      path: string; label: string; value: unknown; status: "confirmed" | "inferred" | "defaulted";
      evidence_quote: string | null; mapping: "supported" | "proposed" | "not_emitted" }[] }[];
  questions: { id: string; text: string; choices: { id: string; label: string }[];
               multi: boolean; text_effect: unknown | null; skippable: boolean }[];  // text_effect != null: free text allowed
  suggestions: { id: string; path: string; value: unknown; sources: string[]; rationale: string; status: string }[];
  not_applied: { request: string; reason: string; proposal: string | null; kind: string }[];
  notices: { code: string; path: string; message: string }[];
  warnings: string[];
  candidate: { version: number | null; sha256: string; yaml: string; can_freeze: boolean;
               levels: { level: number; name: string; status: "passed" | "failed" | "skipped"; note: string;
                         issues: { code: string; path: string; message: string; severity: string; fix: any }[] }[] } | null;
  email_approval: string;      // show it exactly as given
  live: boolean; live_version: number | null;
  applied: boolean | null;     // does the MA run the live version? null = not known (MA offline)
  ma_error: string | null;
  versions: { version: number; sha256: string; approved_by: string | null; approved_at: string | null; live: boolean }[];
  ma_events: { kind: string; severity: string; code: string; message: string; occurred_at: string; repeat_count: number }[];
};
```

The exact model is `src/campaign_agent/view.py`.

### 3.2 What to show for each state

Poll `GET /api/campaigns/:id` **every 2 seconds while the newest request is `pending` or `processing`**, or while the state is a
working state; stop when it settles. Always keep showing the last good data while you poll.

| `state` | Screen | The user can |
|---|---|---|
| `NEW`, `DRAFT` | "starting..." (the CA has not read it yet) | wait |
| `INTERPRETING`, `RESEARCHING`, `VALIDATING` | "working" with the state name | wait |
| `CLARIFICATION_REQUIRED` | the questions | `answer` |
| `MODEL_UNAVAILABLE` | "the language model did not answer" | `interpret` again (a retry) |
| `AWAITING_REVIEW` | **the review** (3.3) | `approve`, `edit`, `suggestion`, `discard` |
| `VALIDATION_FAILED` | the failed checks with their suggested fix | `edit` |
| `PUBLISHED` | live version, who approved it, `applied`, open MA blockers (3.5) | `edit` (a new version), `rollback`, `deactivate` |
| `PUBLISH_FAILED` | "could not publish, try again" | `approve` again |
| `DISCARDED` | closed | nothing |

If the newest request has `status: "failed"`, show `error_detail` (it is written for the user) and keep the screen for the current state.

### 3.3 The review and approval page

Build it in this order:

1. **Fields by section.** Label and value, a badge for `status` (confirmed, inferred, defaulted), the quote it came from, and a
   tag "**not applied yet**" when `mapping` is `proposed` or `not_emitted`.
2. **"Could not be applied"** (`not_applied`). **Always show it, prominently.** The MA cannot do some things yet; hiding this is not allowed.
3. **Notices and warnings.**
4. **Web suggestions**, if any, each with Accept and Reject (`suggestion` request).
5. **The leadgen file**: `candidate.yaml` read only, and the checks `candidate.levels`: L3 schema, L4 business rules, L5 YAML,
   L6 the MA's own loader. Each is passed, failed or skipped; show the issues. If L6 is **skipped**, show its note: it means the
   MA's own loader did not check the file.
6. **The email sentence** (`email_approval`), exactly as given.
7. **Buttons**
   - **Approve** is enabled only when `candidate.can_freeze` is true and no level failed.
   - **Request a change** takes a text box ("also include Austria") and sends an `edit`.
   - **Reject** sends `discard`, after a confirmation. There is no request called "reject".

### 3.4 What each button sends (`POST /api/campaigns/:id/requests`)

Send `request_type`, `payload`, `base_revision` (for `edit`) and a stable `idempotency_key`. The same key for the same campaign
is the same request, so a retry or a double click never makes two.

| Button | `request_type` | `payload` | Idempotency key (example) |
|---|---|---|---|
| Try again (the model did not answer) | `interpret` | `{}` | `interpret-retry-<time>` |
| Send answers | `answer` | `{ "answers": [ { "question_id": "q_..", "value": "c0" } ] }`; value is a choice id, a list of ids, free text, or `"skip": true` instead of a value | `answer-<revision>` |
| Request a change | `edit` | `{ "text": "also include Austria" }` or `{ "ops": [ { "path": "qualification.min_qualify", "value": 55 } ] }`, plus `base_revision` = the revision the user saw | `edit-<revision>-<hash of text>` |
| Accept or reject a suggestion | `suggestion` | `{ "suggestion_id": "s_..", "decision": "accept" }` or `"reject"` | `suggestion-<id>-<decision>` |
| **Approve** | `approve` | `{ "version": <candidate.version>, "sha256": "<candidate.sha256>", "start_pipeline": true }` | `approve-<version>-<first 12 of sha256>` |
| Reject | `discard` | `{}` | `discard-<revision>` |
| Make live (go back to an older approved version, also after a switch-off) | `rollback` | `{ "version": 1 }`; it also starts the MA on it, like an approval; add `"start_pipeline": false` to only make it live | `rollback-<version>-<time>` |
| Switch the campaign off | `deactivate` | `{}` | `deactivate-<time>` |

**Approving, exactly:**

- Send the **`version` and `sha256` from the `candidate` the user was looking at.** If the file changed in the meantime the request
  fails with `stale_approval` and `result.current` holds the new one: show it again for review. Never approve what the user did not see.
- **`start_pipeline: true`** makes approving also run the MA on the new campaign (if the MA was already running it is stopped and
  started again so it takes the new file). `false` or missing only publishes. We want **`true`**: approving means "run this campaign".
  After it finishes, `last_request.result.pipeline` says what happened (`asked`, `restarted`, or a `reason`). The campaign is live either way.
- Only one campaign is live at a time. Approving another replaces it; the old one keeps its versions and can come back with `rollback`.
- **Switching a campaign off (`deactivate`) also stops the MA** (the pass in progress is cancelled). The campaign list's **Active slider** is the dashboard's own flag and does not reach the CA by itself: when it is turned off for the live campaign, your server should send `deactivate` for that campaign too (and only for the live one, so it never stops another campaign's work). It does not stop on its own otherwise: with no campaign live the MA would fall back to its own default settings. Pressing it again when nothing is live still stops a running MA. To run the campaign again, approve it again with `start_pipeline: true` (or use rollback to make an older version live, then start it).

**Errors to handle** (`last_request.error_code`):

| Code | What to do |
|---|---|
| `stale_approval` | the file changed; show `result.current` and ask again |
| `stale_revision` | reload, then let the user retry (`result.revision` is the current one) |
| `missing_base_revision` | you sent an `edit` without `base_revision` |
| `wrong_state` | not allowed in this state; reload |
| `invalid_answer` | an answer was refused; `result.errors` says which |
| `input_rejected` | the text was empty, too long, not English, or looked like a secret |
| `publish_blocked` | the checks no longer pass, or the MA cannot load this file version; show `result.issues` |
| `publish_failed` | nothing was applied; the same approval can be sent again |
| `model_unavailable` | send `interpret` again |
| `rate_limited` | more than 30 requests from one user in a minute; wait and retry |

HTTP errors from your own route: `401` not signed in, `422` a bad request, `503` the database or the gateway is not reachable (try again),
`502` the gateway did not answer.

### 3.5 The CA badge, Pause and Resume, and the MA panel

- **CA badge** (from `GET /api/ca`, poll every 5 seconds while the page is open):
  - `online: false` means the CA service is not running. Show **"the assistant is offline"**. Requests still go through and wait.
  - `status: "paused"` means paused. Show both `control.desired_state` and `control.actual_state`: they differ for a few seconds
    after a click ("pausing...", "resuming...").
- **Pause / Resume button** sends `POST /api/ca/state` with `{ "state": "paused" }` or `"running"`.
  - Paused means the CA takes no new request. A request already running finishes; new ones wait and run, in order, after resume.
  - **Pausing the CA also stops the MA. Resuming starts the MA again only if it was running when the pause came.**
- **MA panel (read only)**, from the campaign's `review_view`:
  - `live`, `live_version`: this campaign is the live one.
  - `applied`: `true` the MA runs it; `false` not yet; `null` not known (the MA is offline and applies it when it returns).
  - `ma_error`: why the MA refused the file, if it did.
  - `ma_events`: every item with `kind: "blocker"` is **an open blocker**: show it as an alert with its `message` and `severity`
    (for example the MA's Gmail login expired, or its browser did not start). It disappears by itself when the MA reports it fixed.

## 4. What the user sees, start to finish

1. They type a campaign. The server saves it and sends `interpret`.
2. The screen shows "working". After a few minutes it shows **questions** (if any), then **the review**.
3. They read the file and the "could not be applied" list, then **approve** (or request a change, which makes a new version, or reject).
4. On approval the campaign is live and, with `start_pipeline: true`, the MA starts working on it. The screen shows `applied`.
5. If the MA gets stuck, a **blocker** appears on the screen. When the MA fixes it, it goes away.
6. At any time a person can **pause** the CA (and with it the MA) and **resume** it.

## 5. What not to do

- Do **not** call the gateway from the browser, and never put the token anywhere the browser can read, or in Git.
- Do **not** take `requested_by` from the request body or the page. It is the approval record, so it comes from your server's login check.
- Do **not** build, show or call a Start or Stop for the MA. Remove the pipeline API calls and the `pipeline_control` table access.
- Do **not** send an approval for a version the user did not see; always send the `version` and `sha256` from the `candidate` shown.
- Do **not** hide "could not be applied", a failed check, a skipped L6, or an open MA blocker.
- Do **not** keep your own copy of the campaign. Render `review_view` and read it again after every request.
- Do **not** show an endless spinner when the CA is offline; check `GET /api/ca`.
- Do **not** promise that approving sends emails. Every email still needs a person's approval in Slack, and while test mode is on the MA sends only to the test address.
- Do **not** write the CA's tables yourself (`campaign_state`, `campaign_version`, `ca_control`, `ma_event`).

## 6. Not built yet (decisions needed)

- Stopping **only** the MA without pausing the CA. Say if you need it; it would be a request handled by the CA.
- Showing "is the MA running right now". Today only what it did for the live campaign (`applied`, `ma_events`) is available.
- The campaign's own on/off switch and dates do not gate the MA unless the owner sets `MA_LINK_RESPECT_SCHEDULE=true`.
- Several campaigns live at once (one at a time for now).

## 7. Other ways in, and where the detail is

You do not need these if you use the gateway.

- **Straight to the database instead of the gateway:** the same requests as rows in `campaign_request`, read from `campaign_state`
  (`docs/handover.md`, `docs/store-and-inbox.md`). If the browser writes to the database directly with Supabase Auth, use
  `migrations/optional/dashboard_auth.sql` so the database checks who is signed in.

| File | For |
|---|---|
| `docs/dashboard-integration.md` (this) | what to build and what not to do |
| `docs/dashboard-api.md` | the gateway's calls, in full |
| `docs/store-and-inbox.md` | every request type, payload and error code |
| `docs/publishing.md` | approval, live, rollback, `start_pipeline` |
| `docs/dashboard-pause-resume.md` | pause and resume in detail |
| `docs/backend-and-dashboard-guide.md` | the whole backend and how it is deployed |
| `src/campaign_agent/view.py` | the exact shape of `review_view` |
