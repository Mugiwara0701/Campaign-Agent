# The dashboard gateway (a small web service on Render)

An **optional** way for the dashboard to talk to the Campaign Agent (CA) over plain HTTPS, instead of writing to the
database itself. The CA stays private on the office machine and does not change: it still polls the database. The gateway
is only a front door in front of the same tables.

```text
 dashboard (hosted) ──HTTPS + token──▶ gateway (Render) ──▶ Supabase ◀── polls ── CA service (office, private)
```

Use it when the dashboard's server prefers one HTTP call over database access. Do **not** call it from a browser (the token
would be visible); call it from the dashboard's **server code**. Everything the CA does stays the same: it checks every
request, a person still approves in the dashboard, and every email is still approved in Slack.

## What it is, and what it is not

- It **records requests** (`interpret`, `answer`, `edit`, `suggestion`, `approve`, `discard`, `rollback`, `deactivate`) and
  **reads back** what to show. It also pauses and resumes the CA.
- It does **not** talk to the office machine, does **not** run the model, does **not** create campaigns (your dashboard
  already does, with the `campaign` table it owns) and does **not** control the Marketing Agent.
- It logs in to the database as the limited `campaign_dashboard` role, never the owner, so it can do no more than the
  dashboard's own limited role: insert requests and read what the CA publishes.
- It **trusts the caller about who the user is** (`requested_by`). Only the dashboard's server has the token, so the
  dashboard's server must fill `requested_by` from its own verified login.

## Calls

Every call except `/health` needs `Authorization: Bearer <API_TOKEN>`. Bodies and answers are JSON. Wrong or missing token:
`401`. Bad body: `422`. Unknown campaign or request: `404`. Database not reachable or a migration missing: `503`.

| Call | What it does |
|---|---|
| `GET /health` | Is the gateway up (no token, no database). |
| `POST /v1/campaigns/{campaign_id}/requests` | Send a request. Returns `202` at once: `{"request_id", "status": "pending", "duplicate"}`. |
| `GET /v1/campaigns/{campaign_id}` | What to show: `state`, `revision`, `review_view`, and the newest request (`last_request`). `state` is `NEW` before the CA has touched it. |
| `GET /v1/requests/{request_id}` | One request: `status` (`pending`, `processing`, `done`, `failed`), `error_code`, `error_detail`, `result`. |
| `GET /v1/ca` | `online` (heartbeat under a minute old), `status`, and `control` (`desired_state`, `actual_state`). |
| `POST /v1/ca/state` | `{"state": "paused" or "running", "requested_by": "..."}`. Pausing the CA also stops the Marketing Agent. |

### Send a request

```json
POST /v1/campaigns/6f1d3a52-0b0e-4f6e-8c52-1f1d9f0f9a11/requests
{
  "request_type": "interpret",
  "payload": {},
  "requested_by": "priya@example.com",
  "idempotency_key": "interpret-1"
}
```

- `request_type`: one of the eight above. Payloads are the same as in `docs/store-and-inbox.md`.
- `payload`: `{}` for `interpret` makes the CA use the campaign's own text. For `approve` send the version and hash the user
  reviewed: `{"version": 2, "sha256": "...", "start_pipeline": true}` (see `docs/dashboard-integration.md`).
- `base_revision`: the `revision` the user was looking at (the CA requires it for `edit`).
- `requested_by`: the signed-in user's email (at most 100 characters). **It is the approval record.**
- `idempotency_key`: any string; the same key for the same campaign returns the first request (`"duplicate": true`). Use a
  stable one, such as `approve-<version>-<hash start>`, so retries and double clicks are safe.

Then poll `GET /v1/requests/{request_id}` every 1 to 2 seconds until `done` or `failed`, and read `GET /v1/campaigns/{id}`.
What each state means and how to build the approval page is in `docs/dashboard-integration.md`; the calls there map one to
one onto these.

### From the dashboard's server (Node)

```js
const BASE = process.env.CA_GATEWAY_URL;            // https://campaign-agent-gateway.onrender.com
const headers = { Authorization: `Bearer ${process.env.CA_GATEWAY_TOKEN}`, "Content-Type": "application/json" };

export async function startCampaign(campaignId, userEmail) {
  const r = await fetch(`${BASE}/v1/campaigns/${campaignId}/requests`, {
    method: "POST", headers,
    body: JSON.stringify({ request_type: "interpret", requested_by: userEmail, idempotency_key: `interpret-${campaignId}` }),
  });
  if (!r.ok) throw new Error(`gateway ${r.status}`);
  return r.json();                                    // { request_id, status: "pending", duplicate }
}
```

## Put it on Render (one time)

1. **Database.** Apply the migrations (`bash deploy/remote.sh migrate --plan` then `migrate`): `0022_dashboard_gateway.sql`
   adds the one permission the gateway needs. Then give the limited role a login:
   `bash deploy/remote.sh agent dashboard-password`. It asks for a password (not shown) and prints the `API_DATABASE_URL`
   shape. Use the **pooler** URL (port 6543) of Supabase, as the CA does; Render cannot reach the direct host.
2. **Render.** New > Blueprint > this repository. It reads `render.yaml` (a free web service; start command
   `python -m campaign_agent api`). Render asks for two secrets:
   - `API_DATABASE_URL`: the URL from step 1.
   - `API_TOKEN`: at least 32 random characters, for example `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
     Give the same value to the dashboard's server as `CA_GATEWAY_TOKEN`. Nobody else.
3. **Check.** `curl https://<your-service>.onrender.com/health` gives `{"status":"ok"}`. Then with the token:
   `curl -H "Authorization: Bearer $TOKEN" https://<your-service>.onrender.com/v1/ca` shows whether the CA is online.

## Limits to know about

- **Render's free plan sleeps** after about 15 minutes without a call; the next call takes 30 to 60 seconds while it wakes.
  The dashboard's server should allow that timeout (or use a paid plan, or a periodic call to `/health`).
- The gateway adds one hop; the CA still takes a request about 2 seconds after it is recorded.
- Rotate `API_TOKEN` by changing it in Render and in the dashboard's server together.
- If the office machine is off, calls still succeed (`202`); the request waits and `GET /v1/ca` says `online: false`. Show
  "the assistant is offline" from that.
- No rate limit of its own: the CA refuses more than 30 requests a minute from one `requested_by` (`rate_limited`).

## Not doing

- Do not put `API_TOKEN` or the database URL into browser code or a public repository.
- Do not send a `requested_by` the user did not sign in as.
- Do not use the owner's database login for the gateway.
