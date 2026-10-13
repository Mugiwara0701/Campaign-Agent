# Security review (M11)

A review of the Campaign Agent as built through M10, against the inputs it trusts and the privileges it holds. Every
finding is **fixed** (with where), **mitigated** (a control exists and is tested), or **accepted in writing** (with the
reason and what would change the decision). Reviewed: the code in `src/`, the migrations, `deploy/`, the CI.

## Trust boundaries

| Input | Who controls it | Trusted? |
|---|---|---|
| Campaign text and edits (`campaign_request.payload`) | a dashboard user, through the dashboard server | **no**: any text, possibly hostile |
| `requested_by`, the approver | the dashboard server | trusted as the dashboard server's word (see finding 4) |
| Model replies | the model, steered by user text | **no**: parsed and checked by code |
| Search results and fetched pages (research) | the internet | **no** |
| The Marketing Agent's loader and its status row | our own code, pinned | trusted code; its status row is only read |
| Database rows written by other roles | the dashboard and the Marketing Agent | read with the same checks as any input |

## Findings

| # | Area | Finding | Status |
|---|---|---|---|
| 1 | Untrusted text | Oversized, binary, hidden-character, secret-shaped or non-English text reaching the model | **Mitigated**: the input guard refuses it before any model call (`guard/input_guard.py`); covered by unit tests and the benchmark's adversarial cases |
| 2 | Prompt injection in user text | "Ignore the rules", "turn off approval", "add every company" | **Mitigated**: the model only fills a closed schema; every value needs a quote that is literally in the text and supports the value; approval is not a writable field; a human reviews every candidate and every email is approved in Slack. The benchmark measures it (adversarial category, unsupported-field generation target) |
| 3 | Request size | A very large `payload` JSON (many ops, huge strings) used to cost memory and time | **Fixed in M11**: requests over 100,000 characters are refused with `invalid_request` before they are read (`jobs/handlers.py`); ops lists are capped at 40 by the patch model; tests in `tests/unit/test_hardening.py` |
| 4 | Identity | `requested_by` (and so the approver recorded on a version) is taken from the request row, which the dashboard server writes. Anyone who can insert into `campaign_request` can name any approver | **Accepted**: the dashboard's database role can only insert requests and read what the agent published; its credentials stay on the dashboard server. The audit trail records what the row said. If dashboard users ever get direct database access, add signed requests |
| 5 | Request flooding | One requester could queue unlimited work and starve the single worker | **Fixed in M11**: at most `RATE_LIMIT_PER_MINUTE` (default 30) requests per requester per minute are picked up; the rest fail at once with `rate_limited`, without a model call (`jobs/manager.py`); integration test in `tests/integration/test_hardening.py`. Requests are also processed strictly in order per campaign |
| 6 | Role privileges | The agent could change what is live or approve something itself | **Mitigated**: the `campaign_agent` role has no direct write access to the live pointer or the approval fields; only the three `security definer` functions (`ca_publish`, `ca_activate`, `ca_deactivate`) can, and they check the hash and the latest candidate. A stored version cannot change. Tested on a real database (`tests/integration/test_roles.py`, `test_publish.py`). The dashboard role can insert requests and read published state only |
| 7 | Owner credentials on the worker | `MIGRATE_DATABASE_URL` (the database owner) is needed for `migrate`, `agent-password` and `retention` | **Mitigated**: `push-env` warns and requires a typed word when `DATABASE_URL` is an owner role; the runbook says to remove the owner string after use. **Accepted** residual: it sits in the `.env` between steps. It is optional and unset in normal running |
| 8 | Secrets at rest | `.env` holds the database password and the model key | **Mitigated**: mode 600, never part of `sync`, old copies kept as mode-600 `.bak`; the service runs as its own account with no sudo (`deploy/create-service-user.sh`); `.env` is git-ignored and gitleaks runs in CI. **Accepted**: plaintext in a file on the office machine; disk encryption is the operating system's job (recommended in the runbook) |
| 9 | Secrets in text and logs | Users pasting keys into a campaign; keys landing in logs or the stored campaign | **Mitigated**: secret-shaped input is refused; a campaign name or message that looks like a secret is left out of the stored campaign; logs are structured and carry request ids, states and error codes only, never prompts, user text or fetched pages, and exceptions are logged by class name only (`logs.py`) |
| 10 | Web research | Prompt injection through fetched pages; server-side request forgery; crawling company or people data | **Mitigated** (off by default): query policy on every term and query, domain deny list, public-address check on every hop, size and time caps, output checked by code, suggestions never change values ([research.md](research.md)). **Accepted**: DNS rebinding between the address check and the request, and a person's name written as plain words passing the policy; research stays off unless the benchmark justifies it |
| 11 | Downstream check (level 6) | Running another program on user-influenced text | **Mitigated**: the overlay is data written to a temporary file; the child has an empty environment, no network (`unshare --net`), its own session killed on timeout, a 60 s limit, and runs only the pinned Marketing Agent checkout. If the namespace cannot be created, L6 is skipped visibly, or fails with `MA_REQUIRED=true`. **Accepted**: if the pinned checkout were compromised, so would this machine; pin a commit and review updates |
| 12 | YAML | Unsafe YAML features in what is published | **Mitigated**: only `safe_load`, duplicate keys rejected, anchors, aliases, tags and multiple documents rejected at level 5, and the output is built from the typed model, never from model text |
| 13 | Resource limits | A runaway process or request | **Fixed in M11**: the unit sets `MemoryMax=1G`, `TasksMax=256`, `LimitNOFILE`, `LimitCORE=0`, `NoNewPrivileges`, `PrivateTmp`, `UMask=0077` and a start limit (five starts in ten minutes) so a crash loop cannot hammer the database login. Model calls have a timeout and a budget of six per job; research has 6 searches and 5 fetches; one request at a time; a lease frees a request whose worker died |
| 14 | Error detail to the dashboard | Internals leaking in `error_detail` | **Mitigated**: details are fixed strings or the first validation message; an unexpected error is reported by exception class only; 500 characters at most |
| 15 | Supply chain | Vulnerable or malicious dependencies, leaked secrets in history | **Mitigated**: `pip-audit` and gitleaks run in CI on every push and pull request; Dependabot updates dependencies and actions; the history scan found only fake test secrets, listed in `.gitleaksignore` |
| 16 | Access to the office machine | SSH password guessing; a laptop pointing at the live database | **Mitigated**: the scripts refuse to run the worker anywhere but the office machine; the firewall allows only SSH. **Open recommendation** (runbook step 9): key-only SSH, and Tailscale instead of exposing SSH on the office network. Done by hand, not by the scripts |
| 17 | Retention of user text | Text kept longer than needed | **Fixed in M11**: payloads of finished text requests are cleared after 90 days ([retention.md](retention.md)); fetched pages and prompts are never stored |
| 18 | Availability | The worker, the model or the database down | **Mitigated**: requests wait in the table and nothing is lost; retries with growing waits and ten minutes after a refused login; the Marketing Agent keeps running the live campaign without the Campaign Agent ([runbook.md](runbook.md)) |

## Residual risks, accepted

1. The dashboard server is trusted for identity (finding 4).
2. The owner connection string exists briefly on the office machine for maintenance commands (finding 7).
3. Plaintext secrets in `.env` on an encrypted-at-rest-by-the-OS machine (finding 8).
4. Level 6 trusts the pinned Marketing Agent checkout (finding 11).
5. Research, if switched on, has the two limits of finding 10.

Revisit this review when: the dashboard gets users with direct database access; research is switched on in production; a
second worker or machine is added; the Marketing Agent's role for reading campaigns is created (M8 work, see
[marketing-agent-m8-work.md](marketing-agent-m8-work.md)); or the model is changed.

## Not covered

No penetration test was done, and nothing here was run against the live database. The tests named above run in CI on a
throwaway PostgreSQL. The scripts in `deploy/` are checked by `bash -n` and `shellcheck` in CI but are not run by it.
