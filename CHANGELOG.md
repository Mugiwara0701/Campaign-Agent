# Changelog

All notable changes to the Campaign Agent. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Each milestone of `docs/project-milestones.md` is one
group below.

## [0.1.0] - 2026-10-13

First release. Turns a plain-language campaign description into a validated, versioned campaign configuration for the
Marketing Agent, through a request inbox in the shared database. See [docs/release.md](docs/release.md) for what has
been proven and what must happen before production use.

### Added

- **Foundations (M0)**: scaffold, CI (lint, types, tests, SQL, shell, markdown, security), decisions record,
  database-inbox architecture (no HTTP server).
- **Contract stand-in (M1)**: strict model of the Marketing Agent's configuration file, field metadata, defaults and
  bounds, golden valid and invalid fixtures.
- **Campaign model and merger (M2)**: the intermediate representation with provenance (confirmed, inferred, defaulted),
  closed patch operations, lock rules, evidence-quote checks.
- **Compiler and validators (M3)**: overlay compiler, deterministic YAML serializer, validation levels 3 to 5, "not
  applied" report for what the contract cannot express.
- **Model and extractor (M4)**: provider-neutral model adapter (Ollama, OpenAI-compatible, scripted stub), one
  extraction call per turn with bounded repair, retry and fallback, input guard.
- **Gaps and conversation (M5)**: deterministic gap and conflict engine, templated questions asked once, conversation
  state machine.
- **Store and inbox (M6)**: tables, request inbox with leases and ordering per campaign, worker, review view, roles
  `campaign_agent` and `campaign_dashboard`, migrations `0013` and `0014`.
- **Publish (M7)**: frozen immutable versions, approval by version and hash, one live campaign, rollback and stop, all
  enforced by database functions (`0015`).
- **Marketing Agent integration (M8, Campaign Agent side)**: level 6 runs the Marketing Agent's loader in an isolated
  child process; approval refused when the Marketing Agent cannot load the contract version; the live campaign shows
  whether it was applied (`0016`); the work list for the Marketing Agent repository.
- **Research (M9)**: bounded concept research behind a flag: query policy, own search instance, safe fetcher,
  summarizer whose output is checked by code, suggestions only.
- **Evaluation (M10)**: 123 labelled benchmark cases with a held-out split, a harness with outcome-based scoring, a
  report against the proposed targets, `python -m campaign_agent bench`.
- **Deployment and hardening (M11)**: office-machine scripts (`deploy/remote.sh`), dedicated service account, sandboxed
  systemd unit, rate limit per requester, request size cap, retention command, runbook, security review, retention policy.
- **Release (M12)**: end-to-end acceptance tests against a stand-in Marketing Agent, a nightly real-model workflow,
  the dashboard handover, this changelog.

### Known limits

- The Marketing Agent does not yet implement its side (loader, `--campaign`, pickup, status row): level 6 is skipped
  and nothing applies a published campaign until it does.
- No model has been measured; the benchmark must be run on the office machine and a model chosen.
- The migrations and the deploy scripts have not been run against the real Supabase database or the real office
  machine.
- Not supported yet (reported as "not applied"): US-only targeting, company size, preferred roles; starting the pipeline
  from an approval; more than one live campaign; languages other than English.

[0.1.0]: https://github.com/Mugiwara0701/Campaign-Agent/releases/tag/v0.1.0
