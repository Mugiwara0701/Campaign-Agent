#!/usr/bin/env bash
# Starts the Campaign Agent worker as one long-running process. Used by campaign-agent.service.
set -euo pipefail
cd "$(dirname "$0")/.."
# The worker must run on the office machine, never on a laptop whose .env may point at the live database.
if [ "${CA_ON_OFFICE_MACHINE:-}" != 1 ]; then
  echo "Not starting: this is not the office machine. From the laptop use: bash deploy/remote.sh up" >&2
  exit 2
fi
[ -f .env ] || { echo "No .env in $PWD. From the laptop run: bash deploy/remote.sh push-env" >&2; exit 1; }
[ -x .venv/bin/python ] || { echo "No .venv in $PWD. From the laptop run: bash deploy/remote.sh setup" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
. ./.env
set +a
exec .venv/bin/python -m campaign_agent start
