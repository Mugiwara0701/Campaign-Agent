#!/usr/bin/env bash
# Opens the Campaign Agent window (docs: python -m campaign_agent ui). Run by the desktop at login
# (deploy/campaign-agent-ui.desktop), or by hand on the office machine.
# One window only: if one is already open this just says so. Closing the window quits it.
set -u
cd "$(dirname "$0")/.." || exit 1
export DISPLAY="${DISPLAY:-:0}"
LOG="$HOME/.cache/campaign-agent-ui.log"
mkdir -p "$(dirname "$LOG")"
sleep "${UI_START_DELAY:-10}"   # let the desktop, the network and the Campaign Agent service come up first
# shellcheck disable=SC1091  # the settings file is on the office machine only
[ -f .env ] && { set -a; . ./.env; set +a; }
exec .venv/bin/python -m campaign_agent ui >>"$LOG" 2>&1
