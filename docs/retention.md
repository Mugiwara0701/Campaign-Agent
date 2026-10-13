# Retention (decision U8)

What the Campaign Agent keeps, for how long, and how it is cleared. Decided in M11; change it here and in
`src/campaign_agent/store/retention.py` together.

## The policy

| Data | Where | Kept | Why | Cleared how |
|---|---|---|---|---|
| The text the dashboard sent with interpret, answer and edit requests (the user's words) | `campaign_request.payload` | **90 days** after the request finished | the user's words may name companies or hold something that should not have been pasted; after review they are not needed | `python -m campaign_agent retention --days 90 --yes` sets the payload to `{}`; the row stays |
| Who asked for what and when, the outcome and error code of every request | `campaign_request` (the row) | as long as the campaign exists | audit: every change of what is live, including rollback and stop, must stay traceable | with the campaign |
| Approvals, publish, rollback and stop requests | `campaign_request` | as long as the campaign exists (their payload holds only a version number and a hash) | audit | with the campaign |
| Versions: the YAML, hashes, validation report, "not applied" list, the snapshot of the campaign, who approved and when | `campaign_version` | as long as the campaign exists; **immutable** | what the Marketing Agent ran must be reproducible | with the campaign |
| The working campaign: values, where each came from, the evidence quote, open questions, suggestions | `campaign_state` | as long as the campaign exists | it is the campaign | with the campaign |
| Web pages fetched by research | **not stored** | not at all | only the source URLs of suggestions are kept, inside the campaign | n/a |
| Prompts and model replies | **not stored** | not at all | not logged either | n/a |
| Logs | the journal | 30 days, at most 1 GB | operations | automatic (journald, set by the bootstrap) |
| Benchmark reports | `benchmarks/results/` | with the repository | they hold the synthetic benchmark texts, no user data | n/a |

Deleting a campaign in the dashboard deletes every row above for it (the tables reference `public.campaign` with
`on delete cascade`). That is the way to honour an erasure request; the retention command is only the time-based clean-up.

## Running it

```bash
bash deploy/remote.sh agent retention --days 90          # counts only, changes nothing
bash deploy/remote.sh agent retention --days 90 --yes    # clears
```

It needs the database **owner**, because the limited `campaign_agent` role may not change request payloads (that is on
purpose). Put the owner string in the office `.env` as `MIGRATE_DATABASE_URL` for the moment it runs and remove it again,
as for migrations. The command refuses fewer than 7 days, prints the target database (never the password), and does
nothing without `--yes`. It only touches finished requests of the three text-carrying kinds.

## What it does not do

- It does not delete rows, versions or campaigns.
- It cannot clear text the dashboard keeps itself (`public.campaign.campaign_detail` belongs to the dashboard).
- The text of an interpret request also lives on in the campaign's own `ir_snapshot` as evidence quotes (short quotes, at
  most 300 characters each) for as long as the campaign exists. That is the evidence the review shows; delete the
  campaign to remove it.
