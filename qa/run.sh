#!/usr/bin/env bash
# Drives the acceptance and soak test on the two office machines. Nothing runs on this laptop except ssh and rsync.
#
#   bash qa/run.sh deploy                 send the qa folder to both machines
#   bash qa/run.sh start [--hours 4.5] [--quick] [--only a,b]   start both runners (detached), prints the run id
#   bash qa/run.sh status                 progress of the current run on both machines
#   bash qa/run.sh stop                   ask both runners to finish the test they are in and stop
#   bash qa/run.sh collect                copy the results back to qa/results/<run>/{ca,ma}
#   bash qa/run.sh report                 collect, build the PDF on the CA machine, copy it back
#   bash qa/run.sh all [--hours 4.5]      EVERYTHING in one go: stop the MA, deploy, run both sides, wait, build the PDF
#   bash qa/run.sh run <side> <args...>   run one side in the foreground (e.g. run ca --quick --only ca.health)
set -euo pipefail

cd "$(dirname "$0")/.."
CA_REMOTE="$(. ./.remote.env && echo "$REMOTE")"
CA_DIR="$(. ./.remote.env && echo "${REMOTE_DIR:-Desktop/Campaign-Agent}")"
MA_ENV="${MA_REMOTE_ENV:-$HOME/Desktop/Marketing-AI-Agent/.remote.env}"
MA_REMOTE="$(. "$MA_ENV" && echo "$REMOTE")"
MA_DIR="$(. "$MA_ENV" && echo "${REMOTE_DIR:-Desktop/Marketing-AI-Agent}")"
# Results of the CA side live OUTSIDE the repo folder: `deploy/remote.sh sync` deletes everything in it that is not code.
CA_RUNS='$HOME/qa-runs'                    # expanded on the CA machine
MA_QA_DIR="qa-suite"                       # on the MA machine; the MA repo itself is never touched
STATE="qa/results/.current"                # the run id of the latest start
SSH=(ssh -o ServerAliveInterval=30 -o ConnectTimeout=15)

ca() { "${SSH[@]}" "$CA_REMOTE" "$@"; }
ma() { "${SSH[@]}" "$MA_REMOTE" "$@"; }
runid() { cat "$STATE" 2>/dev/null || { echo "no run started yet" >&2; exit 1; }; }

deploy() {
  local rs=(rsync -az --delete --exclude results/ --exclude __pycache__/ --exclude .env.qa -e "ssh -o ConnectTimeout=15")
  ca "mkdir -p $CA_DIR/qa"
  "${rs[@]}" qa/ "$CA_REMOTE:$CA_DIR/qa/"
  [ -f qa/.env.qa ] && scp -q qa/.env.qa "$CA_REMOTE:$CA_DIR/qa/.env.qa"
  ma "mkdir -p $MA_QA_DIR/qa"
  "${rs[@]}" qa/ "$MA_REMOTE:$MA_QA_DIR/qa/"
  echo "qa sent to both machines"
}

stop_old() {
  # A second runner on a machine would fight the first (browser, model, database): end any left over.
  ca 'for p in $(pgrep -f "[p]ython -m qa.soak"); do pkill -9 -P $p; kill -9 $p; done; true'
  ma 'for p in $(pgrep -f "[p]ython -m qa.soak"); do pkill -9 -P $p; kill -9 $p; done; true'
}

start() {
  stop_old
  local id out args="$*"
  id="$(date +%Y%m%d-%H%M%S)"
  out="qa-results/$id"
  mkdir -p qa/results
  echo "$id" >"$STATE"
  # The runner is detached with every stream redirected, inside its own brackets: otherwise ssh waits for it to end.
  ca "mkdir -p $CA_RUNS/$id; cd $CA_DIR; (setsid nohup .venv/bin/python -m qa.soak --side ca --run-id $id --out $CA_RUNS/$id/ca $args >$CA_RUNS/$id/ca.stdout 2>&1 </dev/null &); echo started ca"
  ma "mkdir -p $MA_QA_DIR/$out; cd $MA_QA_DIR; (setsid nohup ~/$MA_DIR/.venv/bin/python -m qa.soak --side ma --run-id $id --out $out/ma $args >$out/ma.stdout 2>&1 </dev/null &); echo started ma"
  echo "run id: $id   (bash qa/run.sh status | stop | report)"
}

status() {
  local id out
  id="$(runid)"
  out="qa-results/$id"
  for side in ca ma; do
    echo "== $side =="
    if [ "$side" = ca ]; then
      ca "cd $CA_RUNS/$id/ca 2>/dev/null && tail -n 6 soak.log; wc -l < results.jsonl; pgrep -f '^[^ ]*python -m qa.soak --side ca' >/dev/null && echo RUNNING || echo finished"
    else
      ma "cd $MA_QA_DIR/$out/ma 2>/dev/null && tail -n 6 soak.log; wc -l < results.jsonl; pgrep -f '^[^ ]*python -m qa.soak --side ma' >/dev/null && echo RUNNING || echo finished"
    fi
  done
}

stop() {
  local id out
  id="$(runid)"
  out="qa-results/$id"
  ca "touch $CA_RUNS/$id/ca/STOP"
  ma "touch $MA_QA_DIR/$out/ma/STOP"
  echo "stop requested; each side finishes the check it is in, restores the live campaign and ends"
}

collect() {
  local id dest
  id="$(runid)"
  dest="qa/results/$id"
  mkdir -p "$dest"
  rsync -az -e "ssh -o ConnectTimeout=15" "$CA_REMOTE:qa-runs/$id/ca/" "$dest/ca/"
  rsync -az -e "ssh -o ConnectTimeout=15" "$MA_REMOTE:$MA_QA_DIR/qa-results/$id/ma/" "$dest/ma/"
  echo "results in $dest"
}

report() {
  local id
  id="$(runid)"
  collect
  ca "mkdir -p $CA_RUNS/$id/in && rm -rf $CA_RUNS/$id/in/*"
  rsync -az "qa/results/$id/ca" "qa/results/$id/ma" "$CA_REMOTE:qa-runs/$id/in/"
  ca "cd $CA_DIR && .venv/bin/python -m qa.make_report --ca $CA_RUNS/$id/in/ca --ma $CA_RUNS/$id/in/ma --out $CA_RUNS/$id/report.pdf"
  scp -q "$CA_REMOTE:qa-runs/$id/report.pdf" "qa/results/$id/report.pdf"
  echo "report: qa/results/$id/report.pdf"
}

runside() {
  local side="$1"
  shift
  if [ "$side" = ca ]; then
    "${SSH[@]}" -t "$CA_REMOTE" "cd $CA_DIR && .venv/bin/python -m qa.soak --side ca --out $CA_RUNS/foreground-ca $*"
  else
    "${SSH[@]}" -t "$MA_REMOTE" "cd $MA_QA_DIR && ~/$MA_DIR/.venv/bin/python -m qa.soak --side ma --out qa-results/foreground-ma $*"
  fi
}

stop_ma() {
  local ma_repo="${MA_REPO:-$HOME/Desktop/Marketing-AI-Agent}"
  echo "stopping the Marketing Agent if it is running"
  bash "$ma_repo/deploy/remote.sh" pipeline stop || echo "(could not stop it through the pipeline command; the tests restore its state anyway)"
}

all() {
  local hours=4.5
  [ "${1:-}" = "--hours" ] && hours="${2:-4.5}"
  stop_ma
  deploy
  start --hours "$hours"
  local deadline=$((SECONDS + (${hours%.*} + 2) * 3600))
  echo "running for about $hours hours; leave this window open (Ctrl+C is safe: the runs continue, use status / report)"
  while [ $SECONDS -lt $deadline ]; do
    sleep 300
    local out
    out="$(status 2>/dev/null || true)"
    echo "$(date +%H:%M) $(grep -c RUNNING <<<"$out") of 2 sides still running"
    grep -q RUNNING <<<"$out" || break
  done
  report
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  deploy) deploy ;;
  start) start "$@" ;;
  status) status ;;
  stop) stop ;;
  collect) collect ;;
  report) report ;;
  all) all "$@" ;;
  run) runside "$@" ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
