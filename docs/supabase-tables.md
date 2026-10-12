# Supabase Tables

How the Campaign Agent (CA) uses the database. Source of the existing table:
`How the Campaign Page Saves Data to Supabase.pdf` (dashboard developer). The SQL is in
[../migrations/0001_campaign_agent_tables.sql](../migrations/0001_campaign_agent_tables.sql) (draft, not yet run on Supabase).

## Existing table, not changed

`public.campaign` is owned by the dashboard (`dashboardbackend/migrations/003_campaign.sql`). Confirmed: it is the
**lead-generation** campaign record. We do not alter or replace it; it is the parent of every CA table through `campaign_id`.

| Column | Meaning for the CA |
|---|---|
| `id` | The campaign id used everywhere |
| `campaign_name`, `campaign_detail` | The first user message (name up to 200, detail up to 5000 characters; the input guard uses the same limits) |
| `start_date`, `end_date`, `is_active` | Dashboard-owned schedule and on/off switch |

Only the dashboard's Next.js routes (service key) write it. RLS is on with no policies.

## Tables the CA adds

| Table | Purpose | Written by |
|---|---|---|
| `campaign_request` | Inbox. One row per dashboard action: `interpret`, `answer`, `edit`, `suggestion`, `approve`, `discard`, `rollback`. Holds status, result and error code. Unique on `(campaign_id, idempotency_key)`. | Dashboard inserts; CA updates status and result |
| `campaign_state` | One row per campaign: current state, revision, working IR, the review view shown in the dashboard, candidate version and **active version** pointer. | CA |
| `campaign_version` | Frozen versions: overlay YAML, sha256, reports, approver. Content is immutable; approval fields can be set once (enforced by a trigger). | CA |
| `ca_heartbeat` | Single row, shows the CA machine is alive. | CA |

```text
public.campaign (dashboard)
   ├── campaign_request   many   (dashboard → CA)
   ├── campaign_state     one    (CA working state + active pointer)
   └── campaign_version   many   (frozen, read by the Marketing Agent)
ca_heartbeat              one row
```

All child tables use `on delete cascade`.

## Flow

1. The dashboard server inserts a `campaign_request` row (the browser never touches Supabase). It writes the verified caller identity into `requested_by`.
2. The CA polls pending rows, processes one per campaign, and updates `campaign_state` and the request row.
3. The dashboard reads `campaign_state.review_view` for the review screen.
4. On approval the CA sets the approval fields on the frozen `campaign_version` and moves `campaign_state.active_version_id`.
5. The Marketing Agent reads the active version at its next pass.

## Access

- Dashboard server: service key, as today.
- CA: its own role `campaign_agent` (no service key), column-limited grants, explicit RLS policies. Created `nologin`; an admin sets the password.
- Marketing Agent: read-only role. **Not defined yet**; its role name is needed (TODO in the migration).

## Open points

- **Schedule semantics.** The MA should run a campaign only when `is_active` is true, today is inside `start_date`–`end_date`, and an active version exists. This needs an MA change and allows overlapping campaigns; decide whether that is allowed in the first release or only one campaign may be live.
- **Delete.** Deleting a campaign cascades to its versions and active pointer. Confirm that is wanted, or move to soft delete.
- **Two writers for the text.** Rule proposed: `campaign_detail` is only the first message; every later change is a `campaign_request`.
- **Where migrations live.** The dashboard runs SQL by hand from `dashboardbackend/migrations/`; the MA uses `supabase/migrations/`. Confirm this is the same Supabase project as the MA's database.
- **Dashboard routes.** The developer must add routes to insert requests and read state.
