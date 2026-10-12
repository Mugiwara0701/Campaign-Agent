#!/usr/bin/env bash
# One-shot setup of the office machine that hosts the model and runs the Campaign Agent.
# Run it ON the office machine, from the synced folder, as your normal user (not root):
#   bash deploy/bootstrap-ubuntu.sh        (or, from the laptop: bash deploy/remote.sh setup)
# Safe to re-run. Never overwrites an existing .env and never prints secrets.
#
# Overrides: LLM_MODEL (default qwen3.5:9b), SKIP_MODEL=1 (do not pull the model), SKIP_OLLAMA=1
set -euo pipefail

LLM_MODEL="${LLM_MODEL:-qwen3.5:9b}"
cd "$(dirname "$0")/.."

[ "$(id -u)" -ne 0 ] || { echo "run as your normal user, not root" >&2; exit 1; }
sudo -v

echo "==> 1/5 base packages"
sudo apt-get update
sudo apt-get install -y git curl ca-certificates build-essential openssh-server rsync ufw
sudo systemctl enable --now ssh   # lets the laptop send code here: deploy/remote.sh
sudo ufw allow OpenSSH
sudo ufw --force enable

echo "==> 2/5 Ollama (the model server)"
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

echo "==> 3/5 Python 3.12 environment"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; }
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
[ -d .venv ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

echo "==> 4/5 configuration (.env)"
if [ -e .env ]; then
  chmod 600 .env
  echo ".env exists, left untouched"
else
  echo "no .env yet: send yours from the laptop with: bash deploy/remote.sh push-env" >&2
fi

echo "==> 5/5 service"
sudo loginctl enable-linger "$USER"   # the service keeps running when nobody is logged in
ollama list || true

cat <<EOF

Done. Remaining:
  1. From the laptop: bash deploy/remote.sh push-env      (DATABASE_URL must use the limited campaign_agent role)
  2. From the laptop: bash deploy/remote.sh install-service
  3. From the laptop: bash deploy/remote.sh up            then:  bash deploy/remote.sh status
EOF
