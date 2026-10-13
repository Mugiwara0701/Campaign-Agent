#!/usr/bin/env bash
# One-shot setup of the office machine that hosts the model and runs the Campaign Agent.
#
#   bash deploy/bootstrap-ubuntu.sh        run it from your laptop: it sets up the OFFICE machine (via deploy/remote.sh)
#
# It only installs on the machine it is told is the office machine. Run anywhere else (your laptop) it installs
# nothing: it hands the job to `deploy/remote.sh setup`, which runs it over SSH on the office machine.
# To install on THIS machine on purpose (you are logged in to the office machine yourself):
#   CA_ON_OFFICE_MACHINE=1 bash deploy/bootstrap-ubuntu.sh
#
# Safe to re-run. Never overwrites an existing .env and never prints secrets.
# Overrides: LLM_MODEL (default qwen3.5:9b), SKIP_MODEL=1 (do not pull the model), SKIP_OLLAMA=1
#
# With a dedicated service account (deploy/create-service-user.sh) the work is split, because that account has no sudo:
#   PART=system   needs sudo: packages, firewall, journal rotation, Ollama and the model. Run by an administrator.
#   PART=user     no sudo: Python, the virtual environment, dependencies. Run as the service account (remote.sh setup).
#   PART=all      both (the default: the administrator's own account runs the service).
# From the laptop, as the administrator, the system part needs no copy of the repository on the office machine:
#   ssh -t admin@host 'PART=system CA_ON_OFFICE_MACHINE=1 bash -s' < deploy/bootstrap-ubuntu.sh
set -euo pipefail

LLM_MODEL="${LLM_MODEL:-qwen3.5:9b}"
PART="${PART:-all}"
case "$PART" in all | system | user) ;; *) echo "PART must be all, system or user" >&2; exit 1 ;; esac
# Read from a pipe (bash -s) there is no file to find the repository from.
if [ -f "$0" ]; then cd "$(dirname "$0")/.."; fi

if [ "${CA_ON_OFFICE_MACHINE:-}" != 1 ]; then
  if [ -n "${REMOTE:-}" ] || [ -f .remote.env ]; then
    echo "This is not the office machine ($(hostname)). Running the setup there, via deploy/remote.sh ..."
    exec bash deploy/remote.sh setup
  fi
  {
    echo "Nothing was installed. This script sets up the OFFICE machine, and none is configured yet."
    echo "  1. bash deploy/remote.sh init <office-user>@<office-address>"
    echo "  2. bash deploy/bootstrap-ubuntu.sh      (or: bash deploy/remote.sh setup)"
    echo "To install on THIS machine on purpose: CA_ON_OFFICE_MACHINE=1 bash deploy/bootstrap-ubuntu.sh"
  } >&2
  exit 1
fi

[ "$(id -u)" -ne 0 ] || { echo "run as your normal user, not root" >&2; exit 1; }
[ "$PART" = user ] || sudo -v

if [ "$PART" != user ]; then
  echo "==> 1/6 base packages"
  sudo apt-get update
  sudo apt-get install -y git curl ca-certificates build-essential openssh-server rsync ufw
  sudo systemctl enable --now ssh   # lets the laptop send code here: deploy/remote.sh
  sudo ufw allow OpenSSH
  sudo ufw --force enable

  echo "==> 2/6 journal rotation (the service logs to the journal)"
  sudo mkdir -p /etc/systemd/journald.conf.d
  # one JSON object per line: request ids and states, never prompts or user text. The journal rotates by itself.
  sudo tee /etc/systemd/journald.conf.d/campaign-agent.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=1G
SystemKeepFree=2G
MaxRetentionSec=30day
RateLimitIntervalSec=30s
RateLimitBurst=2000
EOF
  sudo systemctl restart systemd-journald

  echo "==> 3/6 Ollama (the model server)"
  if [ -z "${SKIP_OLLAMA:-}" ]; then
    command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
    sudo mkdir -p /etc/systemd/system/ollama.service.d
    # one model at a time, one request at a time, unloaded after 5 idle minutes
    sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=5m"
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now ollama
    sudo systemctl restart ollama
    if [ -z "${SKIP_MODEL:-}" ]; then
      until curl -sf http://127.0.0.1:11434/api/version >/dev/null; do sleep 1; done
      ollama pull "$LLM_MODEL"
    fi
  fi
fi

if [ "$PART" != system ]; then
  echo "==> 4/6 Python 3.12 environment"
  command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; }
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12
  [ -d .venv ] || uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -e ".[dev]"

  echo "==> 5/6 configuration (.env)"
  if [ -e .env ]; then
    chmod 600 .env
    echo ".env exists, left untouched"
  else
    echo "no .env yet: send yours from the laptop with: bash deploy/remote.sh push-env" >&2
  fi
fi

echo "==> 6/6 service"
if [ "$PART" != user ]; then
  sudo loginctl enable-linger "$USER" || true   # the service keeps running when nobody is logged in
fi
ollama list || true

cat <<EOF

Done. Remaining:
  1. From the laptop: bash deploy/remote.sh push-env      (DATABASE_URL must use the limited campaign_agent role)
  2. From the laptop: bash deploy/remote.sh install-service
  3. From the laptop: bash deploy/remote.sh up            then:  bash deploy/remote.sh status
EOF
