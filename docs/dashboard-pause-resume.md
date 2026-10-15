# Pause and resume the Campaign Agent from the dashboard

No backend is needed for this. The dashboard writes one row through one database function, and the Campaign Agent
service (which already polls the database for requests every couple of seconds) reads it. Migration:
`migrations/0019_ca_control.sql`. It is the same pattern as the Marketing Agent's `pipeline_control`.

## What the dashboard does

| Need | Call | Role |
|---|---|---|
| Pause | `select * from ca_request_state('paused', 'user@example.com')` | `campaign_dashboard` |
| Resume | `select * from ca_request_state('running', 'user@example.com')` | `campaign_dashboard` |
| Show the state | `select desired_state, actual_state, requested_by, requested_at, state_since from ca_control` | `campaign_dashboard` |
| Is it alive | `select last_seen, agent_status from ca_heartbeat` | `campaign_dashboard` |

With the Supabase client: `supabase.rpc('ca_request_state', { p_state: 'paused', p_by: userEmail })`.
The second argument is shown as "who asked"; it is for people, not for security. The dashboard role has no write
grant on the table, only on that function, so it can do nothing else to it.

## What the dashboard shows

- `desired_state` is what was asked. `actual_state` is what the service is doing. They differ for a short while after
  a click (up to a few seconds, or until the request that is running finishes). Show that as "pausing..." or "resuming...".
- `ca_heartbeat.agent_status` is `idle`, `busy` or `paused`. If `last_seen` is older than about a minute the service is
  not running at all, whatever the table says.

## What "paused" means

- The worker takes **no new request**. A request that is already running **finishes** (a model call cannot be cut
  cleanly). Requests sent meanwhile stay `pending` and run, in order, after resume. Nothing is lost.
- **The Marketing Agent is paused too** (migration `0020`): the Campaign Agent asks its pipeline to stop, and while paused
  it keeps it stopped (checked every 30 seconds). On resume it starts the pipeline again **only if it was running when the
  pause came**; one that was already stopped stays stopped. The pass in progress is cancelled when the pipeline stops. The
  link keeps serving the live campaign, so nothing is lost; the Marketing Agent just does no work meanwhile.
- After a restart of the service the wish is still there: a paused Campaign Agent stays paused until resumed.
- Before the migration is applied the service ignores all this and runs as before.
