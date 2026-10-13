# Runbook

How to install the Campaign Agent on a new office machine, upgrade it, go back, and what to do when something is down.
[office-machine.md](office-machine.md) explains the everyday commands in more detail; this document is the order of
things and the emergency procedures. Everything here is run from the **laptop** with `deploy/remote.sh` unless it says
"on the office machine".

## What runs where

| Part | Where | Notes |
|---|---|---|
| Campaign Agent worker | office machine, one systemd **user** service of a dedicated account `campaign-agent` | polls the database, one request at a time |
| Model server (Ollama) | office machine, system service | `http://127.0.0.1:11434`, one model loaded, one request at a time |
| Search instance (SearXNG) | office machine, only if research is switched on | our own, never shared with the Marketing Agent |
| Database | Supabase (shared with the Marketing Agent and the dashboard) | the agent connects as the limited `campaign_agent` role |
| Dashboard | elsewhere | writes requests into `campaign_request`, reads `campaign_state` |

The worker keeps no state of its own: the `.env`, the `.venv` and the code are all it has. The machine can be rebuilt from
this document.

## 1. Fresh install

Prerequisites: an Ubuntu 22.04 or 24.04 machine with an administrator account, SSH reachable from the laptop, internet
access, and the database owner's connection string (for the migrations only).

1. **Create the service account** (no password, no sudo; the administrator's SSH keys are copied to it):

   ```bash
   ssh -t admin@office-host 'sudo bash -s' < deploy/create-service-user.sh
   ```

2. **Install the system parts** (packages, firewall, journal rotation, Ollama, the model), as the administrator:

   ```bash
   ssh -t admin@office-host 'PART=system CA_ON_OFFICE_MACHINE=1 LLM_MODEL=qwen3.5:9b bash -s' < deploy/bootstrap-ubuntu.sh
   ```

3. **Connect the laptop to the service account and send the code:**

   ```bash
   bash deploy/remote.sh init campaign-agent@office-host
   ```

4. **Install Python and the dependencies** as the service account:

   ```bash
   bash deploy/remote.sh exec "PART=user CA_ON_OFFICE_MACHINE=1 bash deploy/bootstrap-ubuntu.sh"
   ```

5. **Migrate the database** with the owner string (see [office-machine.md](office-machine.md) B2). Put the owner string in
   the `.env` as `MIGRATE_DATABASE_URL`, send it, run `bash deploy/remote.sh migrate`, then set the agent's password with
   `bash deploy/remote.sh agent-password`.
6. **Write the real `.env`** (limited `DATABASE_URL`, no `MIGRATE_DATABASE_URL`) and send it:
   `bash deploy/remote.sh push-env`. Other settings that matter: `LLM_MODEL`, `LLM_TIMEOUT_S`, `LEASE_S`,
   `RATE_LIMIT_PER_MINUTE`, and for the downstream check `MA_CHECKOUT`, `MA_PYTHON` (a pinned checkout of the Marketing
   Agent) and `MA_REQUIRED=true` once that exists.
7. **Install and start the service:**

   ```bash
   bash deploy/remote.sh install-service
   bash deploy/remote.sh up
   bash deploy/remote.sh status
   ```

8. **Check it works end to end** without the dashboard: `bash deploy/remote.sh try "Find robotics companies building
   warehouse robots on Linux. Skip anyone in France."`, then `try --clean`.
9. **Lock the machine down:** SSH keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`), the firewall allows
   only SSH (the bootstrap does this), and `.env` is mode 600 (the scripts do this). Remove `MIGRATE_DATABASE_URL` from the
   `.env` if it is still there.

The install is done when `status` shows the service active and the model server up, and the `ca_heartbeat` row has a
recent `last_seen`.

## 2. Upgrade

```bash
git switch main && git pull
bash deploy/remote.sh sync -n          # preview what would be sent
bash deploy/remote.sh migrate --plan   # new SQL files? (needs MIGRATE_DATABASE_URL in the remote .env)
bash deploy/remote.sh migrate          # only if there are some; asks for a typed word
bash deploy/remote.sh deploy           # send the code, install dependencies, restart
bash deploy/remote.sh status
```

Stopping the service lets the request in progress finish (up to three minutes); one that is cut off is retried. Do the
migration **before** the code that needs it, and keep migrations additive (never edit an applied file; add a new one).
After the upgrade run one `try` as in install step 8.

## 3. Roll back

Code only; the database is never rolled back (migrations are additive, so older code runs on a newer schema):

```bash
bash deploy/remote.sh rollback v0.1.0     # a tag or a commit; asks for a typed word
bash deploy/remote.sh status
```

A campaign that was published stays live whatever the code version: what the Marketing Agent runs is a database
pointer. To go back to an earlier campaign version use the `rollback` request from the dashboard (see
[publishing.md](publishing.md)), and `deactivate` to stop a campaign.

## 4. When something is down

| Symptom | Look at | Do |
|---|---|---|
| Requests stay `pending` in the dashboard | `remote.sh status`, the heartbeat row | the service is stopped or failing: read `remote.sh logs`, fix, `up` |
| `model_unavailable` | `remote.sh status` (Ollama and the loaded model) | on the office machine as the administrator: `sudo systemctl restart ollama`, then `ollama list`; the dashboard's "try again" re-sends the request. Nothing is lost: campaigns in `MODEL_UNAVAILABLE` retry, and direct field edits keep working without a model |
| Model out of memory or very slow | `remote.sh status`, `journalctl -u ollama` | use a smaller model (`remote.sh model`, change `LLM_MODEL`, `push-env`, `up`); raise `LLM_TIMEOUT_S` and `LEASE_S` together |
| Database unreachable | `remote.sh logs`: `database error` lines with the first line of the reason | the worker waits and retries (2 s growing to 60 s; ten minutes after a refused login). Nothing is lost: requests wait in the table. Check the Supabase status page and the network |
| `ECIRCUITBREAKER` or `password authentication failed` | logs | stop the service (`down`), fix `DATABASE_URL`, wait about ten minutes, `up` (details in office-machine.md section F) |
| A request fails with `internal_error` or `worker_crashed` | `campaign_request.error_code`, the logs around its id | one bad request does not stop the worker; send it again; if it repeats, keep the request id and the log lines for a bug report (the logs hold no user text) |
| Requests refused with `rate_limited` | the requester in `campaign_request` | a requester sent more than `RATE_LIMIT_PER_MINUTE` in a minute; it clears by itself, and a loop in the dashboard is the usual cause |
| Candidate cannot be approved: level 6 fails | the version's validation report | the Marketing Agent's loader rejected the file: fix the campaign, or the MA checkout is the wrong version (`MA_CHECKOUT`) |
| Level 6 `skipped` although it is needed | `MA_CHECKOUT` unset, or the machine cannot create a network namespace | set `MA_CHECKOUT`; check `unshare --net --map-root-user true` works as the service account; `MA_REQUIRED=true` makes this a failure instead of a skip |
| Disk full | `df -h`, `journalctl --disk-usage` | the journal is limited to 1 GB by the bootstrap; the model files are the large part (`ollama list`, `ollama rm`) |
| The machine rebooted | `remote.sh status` | the user service starts by itself (linger is enabled); if not, `sudo loginctl enable-linger campaign-agent` as the administrator |

If the Campaign Agent is down for a long time the **Marketing Agent keeps running the campaign that is live**: nothing the
agent does is needed for a pass to run. Only new campaigns and edits wait.

## 5. Routine

- **Daily glance:** `remote.sh status`; the `ca_heartbeat` row's `last_seen` is recent.
- **Monthly:** `sudo apt upgrade` as the administrator; `pip-audit` runs in CI on every push, and Dependabot opens pull
  requests; `bash deploy/remote.sh agent retention --days 90` shows what would be cleared, add `--yes` to clear it
  (see [retention.md](retention.md); it needs the owner string in the `.env` for that moment only).
- **Rotating the database password:** `bash deploy/remote.sh agent-password` (asks, never shows), update `DATABASE_URL`,
  `push-env`, `up`. Rotate it when anyone who had it leaves.
- **Changing the model:** run the benchmark first ([evaluation.md](evaluation.md)), then change `LLM_MODEL`.

## 6. Acceptance of a rebuilt machine

A machine rebuilt from this runbook is accepted when: `status` is green; `try` with the example message reaches
`AWAITING_REVIEW` with the expected values and `try --clean` removes it; the logs show no prompts or user text
(`remote.sh logs | grep -c "Find robotics"` prints 0); `bash deploy/remote.sh agent bench --oracle --split all` passes.

## 7. The nightly real-model run (optional)

`.github/workflows/nightly.yml` runs the benchmark with the real model every night and uploads the report. It needs a
GitHub Actions **self-hosted runner** on the office machine, labelled `office-machine`, installed by an administrator
under its own unprivileged account (GitHub's page "Settings, Actions, Runners, New self-hosted runner" gives the
commands). The workflow only runs on a schedule or by hand, never for pull requests, so pull-request code cannot reach
that machine. Set the repository variable `NIGHTLY_ENABLED` to `true` once the runner is online; `NIGHTLY_MODEL` picks the
model. A failed target (`--check`) fails the run, which is the morning signal that a model, prompt or dependency change
broke extraction.
