# Versions, Approval and Going Live (M7)

How a reviewed candidate becomes the campaign the Marketing Agent (MA) runs. SQL: `migrations/0015_campaign_agent_publish.sql`
(draft, not yet run on Supabase). Code: `jobs/handlers.py` (the requests), `jobs/manager.py` (storing them),
`store/repositories.py` (the calls to the database functions).

## What "live" means

`campaign_state.active_version_id` points to the version the MA should run. It is **the only thing that makes a campaign
live**. Approving a campaign sends no email and starts nothing: every email is still approved by a person in Slack, and the
pipeline is still started with the existing Start button. The MA reads the pointer at the start of its next pass (the MA's side of M8 is listed in
[marketing-agent-m8-work.md](marketing-agent-m8-work.md)). Whether it did is shown as `applied` in the view.

## The rules, enforced by the database

The agent's database role has **no direct write access** to the live pointer or to the approval fields. It can only call
three functions (`security definer`), so the rules hold even if the application has a bug:

| Function | Does | Refuses (SQLSTATE) |
|---|---|---|
| `ca_publish(campaign, version, sha256, approver)` | approves the version (who, when, once) and makes it live | `CA001` not the latest candidate, or the text differs; `CA002` no such version; `CA004` no approver |
| `ca_activate(campaign, version)` | makes a version that was approved before live again | `CA003` never approved; `CA002` |
| `ca_deactivate(campaign)` | nothing of this campaign is live | `CA002` |

- **At most one campaign is live.** A unique index guarantees it. Publishing or re-activating one makes the previous live
  campaign not live; it keeps all its versions and stays `PUBLISHED` (it has a published version), with `live = false` in its view.
- A stored version can never change: its YAML, hash, snapshot and numbers are locked by a trigger, and approval can be set once.

## Approving

`approve` must name the **version and the sha256 the user reviewed**. The agent then:

1. accepts it only in `AWAITING_REVIEW` (or `PUBLISH_FAILED`, as a retry);
2. refuses with `stale_approval` unless it is the newest candidate with the same text, and returns the current one;
3. **checks the stored YAML again from its text** (schema, business rules, YAML safety) against the base config as it is now. If it no longer passes, the request fails with `publish_blocked` and the campaign becomes `VALIDATION_FAILED` (not `PUBLISH_FAILED`);
4. in one transaction: approves the version, makes it live, stores the new state. If the store fails the whole transaction rolls back, the state is `PUBLISH_FAILED`, and the same approval can be sent again.

Approving something that is already live is a harmless repeat. The approver recorded is the `requested_by` the dashboard
server sends (the signed-in user), at most 100 characters. Level 6 (the MA's real loader, `validators/downstream.py`) is part of the same check when `MA_CHECKOUT` is set; the
approval is also refused when the MA reported contract versions that do not include ours. See [marketing-agent-m8-work.md](marketing-agent-m8-work.md).

## After publishing

- A change (in words, or direct) starts a **new candidate**, version N+1. Version N keeps running until N+1 is approved.
- Discarding that pending change returns to `PUBLISHED`; the live version is untouched.
- `rollback {"version": n}` makes an earlier **approved** version live again; it never makes an unapproved one live.
  It does not change the conversation: the next edit continues from the latest work, not from the rolled-back version.
- `deactivate` stops the campaign; its versions stay and any approved one can be made live again.

## Audit

Who approved what and when is on the version (`approved_by`, `approved_at`). Every change of what is live, including
rollback and stop, is also a row in `campaign_request` with who asked, when, and the result; those rows are not deleted.
Each candidate also stores a snapshot of the campaign as it was (`ir_snapshot`).

## Decisions and open points

- **One live campaign at a time** for the first release. Several at once needs MA support.
- **`start_pipeline` on an approval** (`true` or `false`, default `false`): when `true`, the Campaign Agent asks for the
  Marketing Agent's pipeline to run **after the publish is committed**, with the same function the Start button calls
  (`request_pipeline`, allowed for its role by migration 0018). A failure there never undoes the publish: the answer
  carries `result.pipeline` (`asked`, `restarted`, and `reason` when it could not). A pipeline that is **already running**
  only takes a new campaign at its next pass, so it is stopped and started again (the stop must be seen first; it waits up
  to 90 seconds, then asks for "running" again anyway, so it is never left stopped). Approving again with the flag, for a
  campaign that is already live, starts it again, in case the first attempt failed. Any other value is `invalid_request`.
- **`deactivate` also stops the Marketing Agent's pipeline**, after the change is committed (`result.pipeline`: `asked`, or `already_stopped`, or a `reason`). A campaign switched off must not keep working, and with nothing live the Marketing Agent would otherwise run its own defaults.
- **The dashboard's `is_active` and start/end dates.** By default they are ignored: approval alone makes a version live.
  With `MA_LINK_RESPECT_SCHEDULE=true` the Marketing Agent is only given the live campaign while the campaign is switched on
  and today lies between its start and end dates; outside that, the link says "no campaign is live" and the Marketing Agent
  runs its own base config (it does not stop). Off by default because campaigns made by the test tools are not "active".
- The migrations are applied to the shared database through 0019 (0020, the MA pausing with the CA, is drafted; `migrations/optional/dashboard_auth.sql` is optional, see `docs/handover.md`).
