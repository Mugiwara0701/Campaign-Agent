#!/usr/bin/env bash
# Starts the Campaign Agent worker as one long-running process. Used by campaign-agent.service.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
# shellcheck disable=SC1091
. ./.env
set +a
exec .venv/bin/python -m campaign_agent start
