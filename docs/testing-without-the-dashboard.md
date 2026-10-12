# Testing the Campaign Agent without the dashboard

Until the dashboard is connected, `deploy/remote.sh try` plays its part: it creates a test campaign, sends the same
requests the dashboard will send (read the message, answer questions, edit, approve), waits for the worker, and prints what
the dashboard would show. It runs on the **office machine**, against the real database, with the real model.

## Before the first test

1. The service is running and alive: `bash deploy/remote.sh status` and the heartbeat row (`ca_heartbeat.last_seen`).
2. The office machine has the latest code: `bash deploy/remote.sh deploy`.
3. The test client creates campaign rows, which the limited `campaign_agent` login is not allowed to do, so it uses the
   owner string while you test. Add this to the `.env` on your laptop, then send it:

   ```text
   MIGRATE_DATABASE_URL=postgresql://postgres.<project-ref>:<owner-password>@<pooler-host>:6543/postgres
   ```

   ```bash
   bash deploy/remote.sh push-env
   ```

   The worker keeps using `DATABASE_URL` (the limited login). Only `try`, `migrate` and `agent-password` use the owner
   string. **When you finish testing**, delete that line, run `push-env` again, and `bash deploy/remote.sh exec "rm -f .env.bak"`.

## A test

```bash
bash deploy/remote.sh try "Find US companies with 500-5,000 employees building automotive infotainment on Android"
```

It sends the message and waits (`... INTERPRETING`). On a CPU-only machine the first answer takes minutes. Then it prints
the review, and asks what to do next, depending on the state:

| State | What you are asked |
|---|---|
| `CLARIFICATION_REQUIRED` | each question in turn: a choice id (`c0`), several (`c0,c2`), your own words, or `skip` |
| `AWAITING_REVIEW` | `a`pprove, `e`dit in words, `d`iscard, `q`uit |
| `PUBLISHED` | nothing: it says which version is live |
| `MODEL_UNAVAILABLE` | try again? |

What to look at in the review:
- **Each field and where it came from**: `inferred` (the model read it, with the quote), `confirmed` (you answered or edited it),
  `defaulted`.
- **"Could not be applied"**: US-only, company size, decision-maker roles, LinkedIn, anything the Marketing Agent can't do yet.
- **The YAML** the Marketing Agent would get, and the validation level results.

Things worth trying: a vague message ("Generate a campaign for our services"); a request with a supplier word ("kiosk makers");
a contradiction ("target automotive, exclude automotive"); asking to turn email approval off; an edit that raises the minimum
score above the draft score; approving, then editing and approving again.

## Safety

- Test campaigns are named `[CA-TEST] ...` and created **inactive**, so nothing mistakes them for real ones.
- **Approving makes that campaign the live one**, and only one can be live, so it replaces any other live campaign. The client
  says so and asks you to type `approve` (or `replace` when another one is live). Do not approve while a real campaign is live.
- Remove everything it created: `bash deploy/remote.sh try --clean` (asks you to type `clean`; deletes only `[CA-TEST]` rows with
  their requests and versions).

## Other ways to look

- `bash deploy/remote.sh status` and `logs -f`: what the worker is doing.
- The database: `select current_state, review_view from public.campaign_state;` and the latest rows of `campaign_request`.
