#!/usr/bin/env bash
# Code on this laptop, run on the office machine that hosts the model. Sends the code (and, when you say so,
# the .env file) over SSH and runs commands there. Nothing runs on the laptop.
#
#   bash deploy/remote.sh init user@office-host   one-time: SSH key, remote folder, saves .remote.env
#   bash deploy/remote.sh deploy --env            send code AND .env, install dependencies, restart the service
#   bash deploy/remote.sh <command> ...           see the list below
#
# Settings come from the environment or .remote.env (git-ignored): REMOTE (user@host) and REMOTE_DIR
# (default Desktop/Campaign-Agent, relative to the remote home). The .env file is never part of `sync`: it is sent
# only by `push-env` (or `deploy --env`), after showing what goes where and asking.
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .remote.env ]; then
  # shellcheck disable=SC1091
  . ./.remote.env
fi
REMOTE="${REMOTE:-}"
REMOTE_DIR="${REMOTE_DIR:-Desktop/Campaign-Agent}"   # relative to the remote home directory
SSH_OPTS=(-o ServerAliveInterval=30 -o ControlMaster=auto -o ControlPersist=10m -o "ControlPath=$HOME/.ssh/cm-%r@%h:%p")
SERVICE=campaign-agent.service

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Commands:
  init user@host        one-time setup: SSH key, remote folder, first sync
  sync [rsync opts]     send the code (no .git, .venv, .env, caches). 'sync -n' only shows what would be sent
  push-env [file...]    send .env (or the files named) to the office machine; asks first, keeps a .bak copy there
  setup                 on the office machine: install Python 3.12, Ollama, the model and the dependencies
  deps                  sync, then install/update the Python dependencies there
  install-service       one-time: install the long-running service (campaign-agent)
  deploy [--env]        sync [+ push-env], install dependencies, restart the service
  rollback <git-ref>    put the code of an earlier commit or tag on the office machine and restart (see docs/runbook.md)
  up                    sync, then (re)start the service
  down                  stop the service
  status                is the service running, is the model server up, the last log lines
  logs [-f]             the service log (-f: follow it live)
  migrate [--plan]      apply the SQL files in migrations/ to the database from the office machine's .env.
                        Shows what it would do and asks you to type 'migrate' first; --plan only shows it
  agent-password        set the login password of the limited campaign_agent role (asked there, not shown)
  try "message"         test the agent without the dashboard: sends your message as a test campaign, shows what the
                        agent made, lets you answer its questions, edit and approve. try --clean removes the tests
  agent <args>          python -m campaign_agent <args> there, no sync   (e.g. agent once)
  model [name]          pull the model on the office machine (default: LLM_MODEL from the remote .env)
  test                  sync, then run the tests there (database tests need TEST_DATABASE_URL there)
  exec <cmd...>         run any shell command in the remote repo
  shell                 interactive shell in the remote repo
EOF
}

need_remote() {
  [ -n "$REMOTE" ] || { echo "REMOTE is not set: run 'bash deploy/remote.sh init user@host' first" >&2; exit 1; }
}

# shellcheck disable=SC2029  # the command string is built on this side on purpose
rssh() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }

# Run a command in the remote repo with .env loaded.
remote_run() {
  local cmd
  cmd=$(printf '%q ' "$@")
  # shellcheck disable=SC2016  # expanded on the office machine, on purpose
  rssh -t "cd ~/$REMOTE_DIR && set -a && { [ ! -f .env ] || . ./.env; }; set +a; export PATH=\$HOME/.local/bin:\$PATH; $cmd"
}

do_sync() {
  need_remote
  rssh "mkdir -p ~/$REMOTE_DIR"
  rsync -az --delete --info=stats0,name1 -e "ssh ${SSH_OPTS[*]}" "$@" \
    --exclude='.git/' --exclude='.venv/' --exclude='.env' --exclude='.env.*' --exclude='.remote.env' \
    --exclude='credentials.json' --exclude='credential.json' --exclude='token.json' \
    --exclude='out/' --exclude='.cache/' --exclude='__pycache__/' --exclude='.*_cache/' \
    --exclude='.coverage' --exclude='htmlcov/' --exclude='node_modules/' --exclude='*.pyc' \
    --exclude='*.egg-info/' \
    "${SYNC_SRC:-.}/" "$REMOTE:$REMOTE_DIR/"
}

# A database role that owns the tables is far more than the agent needs. Warn, and ask for a typed word.
check_database_role() {
  local file="$1" url user host
  url=$(grep -E '^DATABASE_URL=' "$file" | head -1 | cut -d= -f2- | tr -d "\"'" || true)
  if [ -z "$url" ]; then
    echo "note: $file has no DATABASE_URL; the service cannot start without one." >&2
    return 0
  fi
  user=$(printf '%s' "$url" | sed -E 's#^[a-zA-Z+]+://([^:@/]+).*#\1#')
  host=$(printf '%s' "$url" | sed -E 's#^[a-zA-Z+]+://[^@]*@([^:/?]+).*#\1#')
  echo "DATABASE_URL: user '$user' on host '$host' (the password is not shown)"
  case "$user" in
    postgres | postgres.*)
      echo "WARNING: that is the database OWNER role. The agent should use the limited 'campaign_agent' role"
      echo "         (user 'campaign_agent' or 'campaign_agent.<project-ref>'). The owner can change anything." >&2
      read -r -p "Type 'owner' to send it anyway, anything else to cancel: " word
      [ "$word" = owner ] || { echo "cancelled" >&2; return 1; }
      ;;
  esac
}

do_push_env() {
  need_remote
  local files=("$@")
  [ ${#files[@]} -gt 0 ] || files=(.env)
  local f
  for f in "${files[@]}"; do
    [ -f "$f" ] || { echo "no such file: $f" >&2; return 1; }
  done
  echo "About to copy to $REMOTE:~/$REMOTE_DIR/ : ${files[*]}   (secrets; mode 600 there, old copy kept as .bak)"
  for f in "${files[@]}"; do
    [ "$(basename "$f")" != .env ] || check_database_role "$f" || return 1
  done
  read -r -p "Send? [y/N] " yn
  [ "$yn" = y ] || { echo "cancelled" >&2; return 1; }
  rssh "mkdir -p ~/$REMOTE_DIR"
  for f in "${files[@]}"; do
    local name
    name=$(basename "$f")
    rssh "cd ~/$REMOTE_DIR && [ ! -f '$name' ] || { cp -p '$name' '$name.bak' && chmod 600 '$name.bak'; }"
    scp "${SSH_OPTS[@]}" "$f" "$REMOTE:$REMOTE_DIR/$name"
    rssh "chmod 600 ~/$REMOTE_DIR/'$name'"
  done
  echo "done: ${files[*]} copied (mode 600). On the office machine LLM_BASE_URL should be http://127.0.0.1:11434"
}

cmd="${1:-help}"
[ $# -eq 0 ] || shift

case "$cmd" in
  init)
    [ $# -eq 1 ] || { echo "usage: remote.sh init user@host" >&2; exit 1; }
    REMOTE="$1"
    mkdir -p "$HOME/.ssh"
    [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
    ssh-copy-id -i "$HOME/.ssh/id_ed25519.pub" "$REMOTE"
    printf 'REMOTE=%s\nREMOTE_DIR=%s\n' "$REMOTE" "$REMOTE_DIR" > .remote.env
    do_sync
    echo "Ready. Next: bash deploy/remote.sh setup   then   bash deploy/remote.sh push-env   then   bash deploy/remote.sh install-service"
    ;;
  sync) do_sync "$@" ;;
  push-env | push-secrets) do_push_env "$@" ;;
  setup)
    do_sync
    # needs the sudo password on the office machine, so a terminal is attached
    rssh -t "cd ~/$REMOTE_DIR && CA_ON_OFFICE_MACHINE=1 bash deploy/bootstrap-ubuntu.sh"
    ;;
  deps)
    do_sync
    remote_run uv pip install --python .venv/bin/python -e ".[dev]"
    ;;
  install-service)
    do_sync
    # shellcheck disable=SC2016  # expanded on the office machine, on purpose
    rssh "mkdir -p ~/.config/systemd/user && sed 's#%h/Campaign-Agent#'\$HOME/$REMOTE_DIR'#g' ~/$REMOTE_DIR/deploy/campaign-agent.service > ~/.config/systemd/user/$SERVICE && systemctl --user daemon-reload && systemctl --user enable $SERVICE && (loginctl enable-linger \"\$USER\" 2>/dev/null || echo 'note: run once on the office machine: sudo loginctl enable-linger \$USER  (so the service runs without a login)') && echo 'installed: $SERVICE. Start it with: bash deploy/remote.sh up'"
    ;;
  deploy)
    do_sync
    if [ "${1:-}" = "--env" ]; then do_push_env; fi
    remote_run uv pip install --python .venv/bin/python -e ".[dev]"
    rssh "systemctl --user restart $SERVICE && sleep 3 && systemctl --user --no-pager status $SERVICE | head -5"
    ;;
  rollback)
    [ $# -eq 1 ] || { echo "usage: remote.sh rollback <git-ref>   (a tag, a commit, a branch)" >&2; exit 1; }
    git rev-parse --verify --quiet "$1^{commit}" >/dev/null || { echo "no such commit: $1" >&2; exit 1; }
    sha=$(git rev-parse --short "$1^{commit}")
    echo "About to replace the code on $REMOTE with commit $sha ($1). Database migrations are not undone."
    read -r -p "Type 'rollback' to continue: " word
    [ "$word" = rollback ] || { echo "cancelled" >&2; exit 1; }
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    git archive "$1^{commit}" | tar -x -C "$tmp"
    SYNC_SRC="$tmp" do_sync
    remote_run uv pip install --python .venv/bin/python -e ".[dev]"
    rssh "systemctl --user restart $SERVICE && sleep 3 && systemctl --user --no-pager status $SERVICE | head -5"
    echo "now running commit $sha"
    ;;
  up)
    do_sync
    rssh "systemctl --user restart $SERVICE && sleep 3 && systemctl --user --no-pager status $SERVICE | head -5"
    ;;
  down)
    need_remote
    rssh "systemctl --user stop $SERVICE && echo 'service stopped'"
    ;;
  status)
    need_remote
    # shellcheck disable=SC2016  # expanded on the office machine, on purpose
    rssh "systemctl --user --no-pager status $SERVICE | head -5; echo; echo -n 'model server: '; systemctl is-active ollama 2>&1; curl -s -m 3 http://127.0.0.1:11434/api/ps | head -c 200; echo; echo; journalctl --user -u $SERVICE -n 15 --no-pager -o cat"
    ;;
  logs)
    need_remote
    if [ "${1:-}" = "-f" ]; then
      rssh -t "journalctl --user -u $SERVICE -f -o cat"
    else
      rssh "journalctl --user -u $SERVICE -n 100 --no-pager -o cat"
    fi
    ;;
  migrate)
    do_sync   # the migrations go with the code
    remote_run .venv/bin/python -m campaign_agent migrate "$@"
    ;;
  try)
    do_sync   # the test client goes with the code
    remote_run env CA_ON_OFFICE_MACHINE=1 .venv/bin/python -m campaign_agent try "$@"
    ;;
  agent-password)
    need_remote
    remote_run .venv/bin/python -m campaign_agent agent-password
    ;;
  agent)
    need_remote
    remote_run .venv/bin/python -m campaign_agent "$@"
    ;;
  model)
    need_remote
    case "${1:-}" in
      *[!A-Za-z0-9._:/-]*) echo "not a model name: ${1:-}" >&2; exit 1 ;;
    esac
    # shellcheck disable=SC2016  # expanded on the office machine, on purpose
    rssh -t "cd ~/$REMOTE_DIR && name=\"${1:-}\"; if [ -z \"\$name\" ] && [ -f .env ]; then name=\$(grep -E '^LLM_MODEL=' .env | head -1 | cut -d= -f2- | tr -d \"'\\\"\"); fi; name=\"\${name:-qwen3.5:9b}\"; echo \"pulling \$name\"; ollama pull \"\$name\""
    ;;
  test)
    do_sync
    remote_run .venv/bin/python -m pytest -q "$@"
    ;;
  exec)
    need_remote
    [ $# -gt 0 ] || { echo "usage: remote.sh exec <command...>" >&2; exit 1; }
    rssh -t "cd ~/$REMOTE_DIR && $*"
    ;;
  shell)
    need_remote
    rssh -t "cd ~/$REMOTE_DIR; exec \$SHELL -l"
    ;;
  help | -h | --help) usage ;;
  *) echo "unknown command: $cmd" >&2; usage >&2; exit 1 ;;
esac
