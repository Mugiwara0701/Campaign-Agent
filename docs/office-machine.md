# Running the Campaign Agent on the office machine

The Campaign Agent (CA) runs on its own office machine, together with the model server (Ollama). You work on the
laptop; `deploy/remote.sh` sends the code and the `.env` file there over SSH and controls the service. Nothing runs on
the laptop: no model, no tests, no database. This is the same pattern as the Marketing Agent's `deploy/remote.sh`.

- **Laptop:** edit, commit, push, and run `deploy/remote.sh`. Never edit files on the office machine: the next sync
  overwrites them.
- **Office machine:** Ollama and the model, the Python environment, and the `campaign-agent` service.

## A. One-time setup

1. **Office machine:** install Ubuntu, create your user, note the address and the username:

   ```bash
   sudo apt install -y openssh-server rsync && sudo systemctl enable --now ssh
   hostname -I        # for example 192.168.1.60
   whoami
   ```

2. **Laptop:** connect (asks for the office user's password once, to copy your SSH key), then send the code:

   ```bash
   bash deploy/remote.sh init user@192.168.1.60
   ```

   Different networks: install Tailscale on both machines and use the Tailscale address.
3. **Install everything on the office machine** (Python 3.12, Ollama, the model, dependencies). It asks for the
   office machine's sudo password and downloads the model there, which takes a while:

   ```bash
   bash deploy/bootstrap-ubuntu.sh      # from the laptop: it runs on the office machine
   ```

   `bash deploy/remote.sh setup` is the same thing. `deploy/bootstrap-ubuntu.sh` never installs on the machine you run
   it from: run anywhere but the office machine it hands the job to `remote.sh setup`, or stops with a message if no
   office machine is configured yet (run `init` first). To install on the machine you are logged in to, on purpose:
   `CA_ON_OFFICE_MACHINE=1 bash deploy/bootstrap-ubuntu.sh`.

4. **Send the `.env` file.** It shows what goes where and asks before copying:

   ```bash
   bash deploy/remote.sh push-env
   ```

5. **Install and start the service:**

   ```bash
   bash deploy/remote.sh install-service
   bash deploy/remote.sh up
   bash deploy/remote.sh status
   ```

## B. What the `.env` on the office machine needs

| Key | Value there |
|---|---|
| `DATABASE_URL` | the **`campaign_agent`** role (user `campaign_agent`, or `campaign_agent.<project-ref>` on Supabase), never the database owner. Behind Supabase's pooler use the transaction URL (port 6543) |
| `LLM_BASE_URL` | `http://127.0.0.1:11434` (the model server on the same machine) |
| `LLM_MODEL` | for example `qwen3.5:9b`; `bash deploy/remote.sh model` pulls it |
| `LLM_TIMEOUT_S` | on a CPU-only machine raise it (a call can take minutes); see below |
| `LLM_THINK` | `false` (the default) for structured extraction |

`push-env` checks `DATABASE_URL` before sending: if it is the owner role (`postgres`, `postgres.<ref>`) it warns and
makes you type `owner` to continue. The password is never printed. The old `.env` on the office machine is kept as
`.env.bak` (mode 600). The `campaign_agent` role is created by migration `0013` and needs a password set by whoever owns
the database; that has to happen before the service can log in.

## C. Daily use (from the laptop)

```bash
bash deploy/remote.sh deploy --env   # send code and .env, install dependencies, restart the service
bash deploy/remote.sh deploy         # code only (the usual case)
bash deploy/remote.sh sync -n        # preview what would be sent, send nothing
bash deploy/remote.sh status         # service, model server, last log lines
bash deploy/remote.sh logs -f        # follow the log
bash deploy/remote.sh agent once     # take at most one request and exit (a quick check on the machine)
bash deploy/remote.sh model          # pull the model named in the remote .env
bash deploy/remote.sh test           # run the tests ON the office machine
bash deploy/remote.sh down           # stop the service
```

## D. What is and is not sent

`sync` sends the repository (code, docs, migrations, deploy scripts) and **never** the `.env` files, `.remote.env`,
credentials, `.git`, `.venv`, caches or build output. Files that exist only on the office machine (its `.env`, its
`.venv`) are not deleted by a sync. The `.env` goes only through `push-env` (or `deploy --env`).

## E. Behaviour to know

- The service is a systemd **user** service with linger enabled, so it keeps running when nobody is logged in and
  restarts after a failure or a reboot. `down` stops it until the next `up`.
- Stopping it (SIGTERM) lets the request in progress finish first, up to three minutes. A request that is cut off is
  released after its lease and tried again, so nothing is lost or half-applied.
- Ollama runs as a system service on the office machine (`systemctl is-active ollama`). `OLLAMA_NUM_PARALLEL=1` and
  `OLLAMA_MAX_LOADED_MODELS=1` keep memory use predictable.
- **CPU-only machine:** a 9B model answers slowly (minutes per request). Raise `LLM_TIMEOUT_S` and `LEASE_S`, or use a
  smaller model for testing. Measuring this is the job of the evaluation milestone (M10).

## F. Troubleshooting

- **`Connection timed out`:** not on the same network. Use Tailscale, or check `ping` and the firewall
  (`sudo ufw allow OpenSSH`).
- **`Permission denied (publickey)`:** run `init` again, or `ssh-copy-id user@host`.
- **The service starts and stops:** `bash deploy/remote.sh logs`. Usually `DATABASE_URL` is missing or the
  `campaign_agent` role has no password yet.
- **`model_unavailable` in the dashboard:** `bash deploy/remote.sh status` shows whether Ollama is up and the model loaded.
- **The service does not survive a logout:** run once on the office machine `sudo loginctl enable-linger $USER`.

## G. Scripts never act on the laptop

- `deploy/bootstrap-ubuntu.sh` installs only on the office machine (see A3).
- `deploy/start-agent.sh`, the service's entry point, refuses to start anywhere but the office machine, because a laptop
  `.env` may point at the live database. The systemd unit sets `CA_ON_OFFICE_MACHINE=1`; from the laptop use
  `bash deploy/remote.sh up`.

## H. Checks

CI runs `bash -n` and `shellcheck` on the scripts in `deploy/`. They are checked there, never run by CI, because they
log in to the office machine.
