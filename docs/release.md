# Release v0.1.0

What "released" means for the Campaign Agent, what has been proven, and what must happen first. The milestones are
merged; this page is the go or no-go list, so nobody has to guess what is still open.

## Acceptance (M12)

| Exit criterion | How it is shown | Where |
|---|---|---|
| Every example campaign of the design review gives the expected result, including the "not applied" report | the robotics example, the US-and-size example (US-only and size reported, not emitted) and the vague message (asks, then reaches review) run end to end on a real PostgreSQL with a stub model | `tests/e2e/test_acceptance.py` |
| The queries change when a campaign is published | the stand-in Marketing Agent's queries before and after publishing differ and contain the campaign's industry, product and technology, and not the excluded country | same |
| An invalid campaign changes nothing | a rejected candidate is never frozen; a rejected change leaves the live version and its YAML untouched | same |
| The Marketing Agent offline during publish | the view says `applied: null`, and becomes `true` when its status row appears | same |
| Downstream acceptance of published versions is 100% | every benchmark campaign that passes levels 3 to 5 is accepted by the loader (about 90 cases) | same |
| Stub-model run in CI, real-model run nightly | CI runs all of the above on every push; `.github/workflows/nightly.yml` runs the benchmark with the real model on the office machine's runner | `ci.yml`, `nightly.yml` |

**Honest limits.** The downstream check ran against a **stand-in** Marketing Agent (`tests/e2e/fake_ma/`) written from the
agreed command, not against the real one: the Marketing Agent does not have the loader, `--campaign` or the pickup yet
([marketing-agent-m8-work.md](marketing-agent-m8-work.md)). "100% accepted" therefore means "100% by the contract as we
wrote it". The same tests must pass against the real Marketing Agent before production.

## Before the first production use (blocking)

1. **Apply the migrations `0013` to `0016`** to the shared Supabase database, as its owner, after review
   (`deploy/remote.sh migrate --plan`, then `migrate`). Nothing in production has run them.
2. **Marketing Agent side of M8:** the strict loader, `--campaign`, pickup of the live version, the status row and its
   grants. Until then a published campaign is not applied by anything.
3. **Install the office machine from the [runbook](runbook.md)** on the real machine (never exercised yet), with the
   dedicated account, and run `deploy/remote.sh try` once.
4. **Run the benchmark with the real model** ([evaluation.md](evaluation.md)) and fill in its decision record. No model has
   been measured, so none is chosen yet; `qwen3.5:9b` is only the default of the scripts.
5. **Dashboard connection** ([handover.md](handover.md)).

Not blocking: the nightly runner (set the repository variable `NIGHTLY_ENABLED=true` once a self-hosted runner labelled
`office-machine` exists on the office machine; run the workflow once by hand first), research (off by default), the
fields the Marketing Agent does not support yet.

## Tagging

After the release branch is merged to `main` and the list above is accepted for the first use:

```bash
git switch main && git pull
git tag -a v0.1.0 -m "Campaign Agent 0.1.0"
git push origin v0.1.0
```

`pyproject.toml` already says `0.1.0`; the service reports it in the heartbeat (`ca_heartbeat.agent_version`). Rolling back
to the tag later: `bash deploy/remote.sh rollback v0.1.0`. The changelog is [../CHANGELOG.md](../CHANGELOG.md).
