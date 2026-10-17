# QA acceptance and soak test

A test suite that drives the real Campaign Agent (CA) and Marketing Agent (MA) on their office machines for
hours, and ends with a PDF report. It checks searching, the model, Slack messages and alerts, e-mail to the test
recipient, the link between the two agents, the gateway, pause and resume, and the whole campaign lifecycle.

Nothing runs on the laptop except `ssh` and `rsync`.

## Run it

```bash
bash qa/run.sh all --hours 4.5            # everything in one go: stops the MA, runs both sides, builds the PDF
bash qa/run.sh deploy                    # send qa/ to both machines
bash qa/run.sh start --hours 4.5         # start both sides, detached; prints the run id
bash qa/run.sh status                    # progress
bash qa/run.sh stop                      # finish the current check, restore the live campaign, end
bash qa/run.sh report                    # collect the results and build qa/results/<run>/report.pdf
```

Smoke run, every test once: `bash qa/run.sh start --quick`. One test in the foreground:
`bash qa/run.sh run ca --quick --only ca.health`. List the tests: `bash qa/run.sh run ma --list`.

## Setup

- `reportlab` in the CA virtual environment (`pip install reportlab`, part of the `dev` extra).
- `qa/.env.qa` (git-ignored) on the laptop, sent by `deploy` to the CA machine only:

  ```text
  QA_GATEWAY_TOKEN=the API_TOKEN set on Render
  QA_GATEWAY_URL=https://campaign-agent-maha.onrender.com
  QA_EXPECTED_RECIPIENT=the test mailbox
  ```

  Without the token the gateway tests are skipped, and the report lists them as skipped.

## What it touches

| Area | What happens | Cleaned up |
| --- | --- | --- |
| Test campaigns | Named `[CA-TEST] QA ...`, made through the real request queue | Deleted at the end of each test |
| Live campaign | A test campaign is made live and rolled back | The campaign and the MA state from before are restored at the end, and after each test |
| Pause and resume | The CA is paused for a minute or two | Resumed by the test |
| Slack | Messages tagged `[QA TEST]` in the alerts channel, plus one webhook alert | Messages are deleted again |
| E-mail | One message per run, redirected to the test recipient only | The sent folder is checked for the recipient headers |
| Leads | Made-up `*.test.example` contacts | Removed by `ma.email.cleanup` |
| Search | Query writing, a real 15-minute discovery run, and a real 25-minute MA pass, each about every 100 min, with the visible Chrome on the MA desktop. Real companies and drafts are saved and drafts go to Slack | Not removed: it is the real product output. Mail still goes only to the test recipient |
| Link | Events with codes `MA_QA_` and `MA_QAM_` | Deleted |

While it runs, both office machines are busy (model, Chrome). The real MA pass also posts drafts to Slack.
Do not run it during real campaign work.

## Statuses

`pass`, `warn` (works but is slow or degraded), `fail`, `error` (the test itself broke) and `skip`
(a precondition is missing). The verdict is FAIL with any fail or error, PASS WITH WARNINGS with any warn.

## Output

Each side writes `results.jsonl` (one line per check), `metrics.jsonl` (a sample each minute), `run.json` and
`soak.log` under `qa-results/<run>/<side>` on its machine. `collect` copies them to `qa/results/<run>/`.
