# M8: what the Marketing Agent repository must do

The Campaign Agent (CA) side of M8 is done in this repository. It is written against the interface below. **The Marketing
Agent (MA, `Marketing-AI-Agent`) does not have any of this yet** (checked on `feature/form-detection`: no contract package,
no `--campaign`, no pickup, no status row). Until it does, level 6 shows as `skipped` and nothing is applied by the MA.
This document is the work list for the MA side and the exact interface the CA relies on. It refines B1, B2, B11 and B12 of
[marketing-agent-changes-for-campaign-setup.md](marketing-agent-changes-for-campaign-setup.md).

## What the CA does now (done)

| Piece | Where |
|---|---|
| L6 runs the MA's loader on the overlay text as a child process: empty environment, own session killed on timeout, no network (`unshare --net`), 60 s default timeout. | `validators/downstream.py` |
| L6 runs when a candidate is made and again right before publishing. It is skipped if L3 to L5 failed. A failure stops the candidate from being frozen or approved. | `compiler/build.py`, `jobs/handlers.py` |
| L6 cannot run (no checkout, no way to cut the network): level shows `skipped` with the reason. With `MA_REQUIRED=true` that is a failure instead. | `validators/downstream.py` |
| Approval is refused (`publish_blocked`) when the MA reported the contract versions it loads and ours is not among them. Unknown (never reported, offline) never blocks. | `jobs/handlers.py`, `ma_status.py` |
| The view of the live campaign says whether the MA applied it (`applied`: yes / not yet / not reported) and why it refused it (`ma_error`). Refreshed every 30 s while the worker is idle. | `jobs/manager.py`, `jobs/worker.py`, `view.py` |
| Read path for the MA's report: table `public.ma_campaign_status`. | `migrations/0016_ma_campaign_status.sql` |

CA settings (`.env` on the office machine): `MA_CHECKOUT` (path of a pinned MA checkout), `MA_PYTHON` (its interpreter,
for example `<checkout>/.venv/bin/python`), `MA_TIMEOUT_S`, `MA_REQUIRED`.

## The interface the CA relies on

### 1. Command

```text
<python> -m agent leads queries --campaign <overlay.yaml>
```

- Run with the checkout as the working directory, **no network, no database, an empty environment** (only `PATH`, `HOME`,
  `LANG`). It must not need `.env`, credentials or Supabase to load a campaign file and print queries.
- Exit code `0` and at least one query on stdout: the file was accepted. Anything else is a rejection; the **last line of
  stderr** is shown to the user, so make it one readable sentence (key path and reason).
- The file is the overlay the CA publishes (`config_version`, `project_context`, the campaign-owned keys). The MA merges it
  over its own base config and loads the result with the strict loader. The merge rule must match
  `compiler.merge_onto_base` in this repository (see [contract-standin.md](contract-standin.md)).

### 2. Status row

The MA writes one row to `public.ma_campaign_status` (table created by `migrations/0016_ma_campaign_status.sql`) with its
heartbeat:

| Column | Content |
|---|---|
| `supported_contract_versions` | `text[]`, the `config_version` values its loader accepts, for example `{1.0}`. Exact match. |
| `current_campaign` | `{"id": <campaign uuid>, "version": <n>, "sha256": <of the overlay text>, "applied_at": <iso time>}`; when it refused a version: the same with `"error": "<reason>"` and the version it refused. |
| `reported_at` | when it wrote the row |

The row has `id = true` (a single row): `insert ... on conflict (id) do update`. The MA's database role needs insert and
update on that table only; grant it in the MA's own migration, because the role belongs to the MA repository.

## Work for the MA repository

Order follows the dependencies; every item has the same "done when" as in the changes document.

1. **Strict, versioned loader** (A1, A2, A6). Pydantic model with `extra="forbid"`, `config_version`, ranges and the
   cross-field rules; defaults declared once (A3, A4); `host_delay_seconds` removed or wired (A5). The CA's stand-in model
   (`contract/model.py`) is the starting point: it should be replaced by the MA's published package, not copied again.
2. **Contract package and golden files** (B1). The CA's `tests/fixtures/leadgen/{valid,invalid}` are the first conformance
   cases. Version tag published from the MA repository; the CA pins the same tag as its MA checkout (L6) and later imports it.
3. **Overlay merge and `--campaign <file>`** (B2). Same merge as the CA's. Effective-config hash
   (`compiler.effective_config_hash`) written next to every run report with the campaign id and version.
4. **Command contract above** (`agent leads queries --campaign`): works offline and without credentials; one-sentence last
   stderr line on rejection.
5. **Migration and pickup** (B11).
   - Grant the MA's role `select` on `campaign_state(campaign_id, active_version_id)` and on `campaign_version(id,
     campaign_id, version_no, overlay_yaml, sha256, contract_version)`, with RLS policies (the CA tables have RLS on).
   - At the start of each discovery pass in `service.run_leads()`: read the live campaign
     (`campaign_state.active_version_id is not null`; at most one exists), load the version, check `sha256(overlay_yaml)`
     equals the stored `sha256`, load strictly, merge, and use it **for the whole pass**. Switch only at a pass boundary.
   - On any failure (hash differs, strict load fails, newer contract version): keep the last good campaign (the base config
     if none), put the error in `last_pass`, and report it in the status row. Never run half a campaign.
   - Nothing live: behave exactly as today.
   - Read `is_active` and the start and end dates of the dashboard's `campaign` row as well? **Open decision**, see below.
6. **Status row** (B12). Write `ma_campaign_status` as above, with the same heartbeat as `agent/control.py`; mirror it in
   `docs/pipeline-api.md`.
7. Optional, later: B13 (start the pipeline from an approval), B3 to B5 (US-only, size, roles). Until then the CA reports
   those requests as "not applied".

## Exit criteria (from the milestone), and who proves them

| Criterion | Proof |
|---|---|
| Publish a valid campaign: the MA's `python -m agent leads queries` output changes at the next pass. | On the office machine after items 1 to 6: `deploy/remote.sh try` to approval, then compare the MA's query output before and after. |
| Publish an invalid one: nothing changes and an error is reported. | An overlay the MA's loader rejects but the CA's checks accept (add a key only the MA knows is wrong): L6 blocks it in the CA; a hand-inserted bad version makes the MA keep the last good one and write `error`. |
| MA offline during publish: applied when it returns. | Stop the MA, approve, start the MA, see `applied` become `yes` in the view within a minute. |

## Open decisions

- **`is_active` and the dates** of the dashboard's `campaign` row versus the live pointer (carried over from M7): today the
  pointer alone decides. Decide who combines them; the MA's pickup is the natural place.
- **Contract version matching** is an exact list. Say if a minor version should be accepted by a newer MA automatically.
- **Rollback and the contract version.** The CA checks the MA's contract versions when approving, not when rolling back to
  an older stored version. Add the check if contract versions will really change between releases.
- **Pinning the MA checkout** on the CA machine (a tag or commit), and who updates it.
