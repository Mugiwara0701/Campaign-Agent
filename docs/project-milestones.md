# Campaign Agent — Project Milestones

This is the build plan. Follow it in order. The design lives in
[campaign-setup-agent-architecture.md](campaign-setup-agent-architecture.md) and
[campaign-agent-handoff.md](campaign-agent-handoff.md); the Marketing Agent (MA) work is in
[marketing-agent-changes-for-campaign-setup.md](marketing-agent-changes-for-campaign-setup.md).

## Deployment model (supersedes earlier docs where they differ)

- The Campaign Agent (CA) runs on its **own office machine**, separate from the MA's machine.
- The CA has **no HTTP server**. The dashboard backend writes requests into a Supabase **inbox table**; the CA polls it (like the MA polls `pipeline_control`), processes the request, and writes results back.
- The database is the **only link** between dashboard, CA and MA. Everything is outbound-only.
- The CA has its own Ollama and its own search instance. It holds none of the MA's secrets.
- Where the older docs say "API", "hosting" or "`202` + job", read "inbox table row" and "request status".

## How to use this document

- One **branch per milestone**, named `milestone/mN-<slug>`, cut from `main`.
- Work on the branch, open a PR into `main`, merge only when CI is green and the exit criteria are met.
- Do not start a milestone before its dependencies are merged, except where marked parallel.
- Anything marked **[MA repo]** is work in the Marketing Agent repository, tracked here but not done here.
- Numeric targets are objectives, not measurements.

## Overview

| # | Milestone | Branch | Depends on | Size |
|---|---|---|---|---|
| M0 | Foundations and decisions | `milestone/m0-foundations-and-decisions` | — | S |
| M1 | Contract stand-in and fixtures | `milestone/m1-contract-standin` | M0 | M |
| M2 | IR, provenance and patch merger | `milestone/m2-ir-and-merger` | M1 | M |
| M3 | Compiler, serializer, validators L3–L5 | `milestone/m3-compiler-and-validators` | M2 | L |
| M4 | LLM adapter, input guard, extractor | `milestone/m4-llm-and-extractor` | M2 (parallel with M3) | L |
| M5 | Gap engine, questions, conversation state | `milestone/m5-gaps-and-conversation` | M3, M4 | M |
| M6 | Store, inbox worker, heartbeat | `milestone/m6-store-and-inbox` | M5 | L |
| M7 | Versions, approval and publisher | `milestone/m7-publish` | M6 | M |
| M8 | MA integration (L6, pickup, status) | `milestone/m8-ma-integration` | M7, MA repo work | L |
| M9 | Web research module | `milestone/m9-research` | M4, M6 | M |
| M10 | Evaluation and model selection | `milestone/m10-evaluation` | M5; research part needs M9 | M |
| M11 | Deployment and hardening | `milestone/m11-deployment-and-hardening` | M8 | M |
| M12 | End-to-end acceptance and release | `milestone/m12-release` | all | S |

```text
M0 → M1 → M2 ─┬→ M3 ─┐
              └→ M4 ─┴→ M5 → M6 → M7 → M8 → M11 → M12
                          M4+M6 → M9 ─┘     M5 → M10 ─┘
```

---

## M0 — Foundations and decisions

**Goal:** the repo and the design agree with each other, and the blocking decisions are made.

**Tasks**

- Update the three design docs for the deployment model above: inbox table instead of API, separate CA machine, own search instance, MA code checkout for L6, MA reports its supported contract version in status.
- Fix the inconsistencies found in the doc review (research in MVP vs deferred, discard and publish-failure transitions, "applied" status ownership, candidate vs approved versions, `min_relevance_terms` missing from the IR).
- Rename the scaffold module `api/` to `inbox/`; drop `fastapi` and `uvicorn` from the dependencies.
- Write the inbox table contract: columns, request types, statuses, error codes, idempotency key, `base_revision`. Agree it with the dashboard developer.
- Review `docs/supabase-tables.md` with the dashboard developer: schedule semantics (`is_active`, dates), delete behaviour, one rule for the message text, same Supabase project, who runs the migration, the dashboard routes that write requests.
- Record decisions: U1 (US-only, size, roles), U2 (approval starts pipeline?), U3 (switch at pass boundary), U5 (search provider), U7 (model access), U8 (retention), A5 (contract ownership), commit attribution.
- Turn on branch protection for `main` and require the CI checks.

**Exit criteria**

- Docs contain no contradictions about hosting or the dashboard interface.
- `docs/decisions.md` lists every decision above with an answer or an owner and a date.
- The dashboard developer has signed off the inbox contract.

---

## M1 — Contract stand-in and fixtures

**Goal:** a strict, machine-checkable definition of what a valid campaign configuration is, living in this repo until the MA ships its own.

**Tasks**

- Pydantic model of the current `leadgen.yaml` keys (`extra="forbid"`, ranges, types, `config_version`), marked as a stand-in.
- Base config (`contract/base_config.yaml`) copied from the MA at a recorded commit.
- Field metadata: `blocking`, `default`, `mapping_status`, `vocabulary`, `help_text`, `bounds`.
- Numeric hard maxima for `queries_per_run`, `pages_per_query`, `max_pages_per_run`, `max_pages_per_domain`, `max_new_leads_per_run` (currently undefined in the docs).
- Golden fixtures under `tests/fixtures/leadgen/valid` and `invalid`: the real MA file, typo keys, wrong types, out-of-range values, dead key `host_delay_seconds`.
- Contract tests.

**Exit criteria**

- The real MA `leadgen.yaml` loads; every invalid fixture fails with a key path.
- `accept_service_companies` and other conflicting defaults are declared once, with the choice documented.

---

## M2 — IR, provenance and patch merger

**Goal:** a typed single source of truth for a campaign, with rules for who may change what.

**Tasks**

- `CampaignIR` models for meta, objective, audience, qualification, contacts, exclusions, limits, fixed approval, provenance (`confirmed`, `inferred`, `suggested`, `defaulted`, `unset`).
- `IRPatch` and `Suggestion` schemas (`extra="forbid"`).
- Patch merger: lock rules, null equals missing, web values only `suggested`, unknown fields rejected and reported as unsupported.
- IR hashing and revision counter.

**Exit criteria**

- Unit tests cover every lock and provenance rule, including a patch that tries to overwrite a confirmed value.
- A patch containing an approval-off field is rejected.

---

## M3 — Compiler, serializer, validators L3–L5

**Goal:** the deterministic half of the pipeline, fully testable without a model.

**Tasks**

- Compiler: IR to overlay, merged onto the pinned base. Define merge semantics: list replace vs append, `project_context` vs `search.*` precedence, exclusions additive-only.
- Serializer: `safe_dump`, stable key order, provenance header; no anchors, aliases, tags or multi-document.
- L3 schema validation, L4 business rules (`min_draft >= min_qualify`, `max_pages_per_run >= queries_per_run × pages_per_query`, `results_per_query >= pages_per_query`, bounds, exclusions not shrunk).
- L5: yamllint and round-trip equality.
- "Not applied" report generator (US-only, company size, roles, LinkedIn, approval off).
- Decide what the sha256 covers: overlay text, effective config, or both.

**Exit criteria**

- Same IR and contract version produce byte-identical YAML.
- Every compiled key is in the contract allow-list.
- Each business rule has a passing and a failing test.

---

## M4 — LLM adapter, input guard, extractor

**Goal:** turn user text into an `IRPatch` safely, with the model replaceable.

**Tasks**

- `LLMAdapter` interface, `OllamaAdapter`, `OpenAICompatAdapter`, capability negotiation (schema mode, JSON mode, prompt-only).
- L1 input guard: empty, size, charset, language, secret-like strings.
- Extractor: prompt, closed-schema output, `research_needs` from the closed set, one repair call, one fallback, call cap per job (count timeout retries too).
- Stub model for deterministic tests and CI.
- Model-robustness tests: malformed JSON, extra keys, wrong enums, null storms, injection strings.

**Exit criteria**

- CI runs the full extractor path against the stub model.
- A live smoke test against local Ollama passes (not part of CI).
- The model never receives secret-like strings.

---

## M5 — Gap engine, questions, conversation state

**Goal:** the campaign conversation: what to ask, when, and what state it is in.

**Tasks**

- Gap and conflict engine (L2): blocking gaps, contradictions, unsupported requests, target drift.
- Templated question builder: max 3 per turn, never repeated, "skip" takes the default, one question per contradiction.
- State machine, with the fixes from M0: discard from any unpublished state, a defined L6-at-approve outcome, a suggestion accept/reject transition.
- Ambiguity cases: "embedded engineers", "automotive", "Android", "campaign for our services", unscoped preferences.

**Exit criteria**

- Every transition has a test; illegal transitions are rejected.
- The ambiguity set produces the expected questions with zero repeats.

---

## M6 — Store, inbox worker, heartbeat

**Goal:** the CA runs as a service that receives requests from the database.

**Tasks**

- Migrations for the four CA tables `campaign_request`, `campaign_state`, `campaign_version`, `ca_heartbeat`, all children of the dashboard's existing `public.campaign` (see [supabase-tables.md](supabase-tables.md); draft SQL in `migrations/0001_campaign_agent_tables.sql`). Run on Supabase and verified.
- DB roles: dashboard (insert requests, read views), Setup part, Publisher, each minimal. Mirror the MA's `0012_pipeline_api_role.sql`.
- Poll loop: claim with `FOR UPDATE SKIP LOCKED`, one running request per campaign, idempotency key, `base_revision` concurrency, error codes in the request row.
- Entry point `python -m campaign_agent start`; heartbeat row.
- Local development with SQLite or a local Postgres.

**Exit criteria**

- Integration tests: duplicate request ignored, stale `base_revision` fails with an error code, crash mid-request is retried safely.
- The dashboard role cannot read or write anything outside the campaign tables.

---

## M7 — Versions, approval and publisher

**Goal:** an approved campaign becomes the active one, exactly as reviewed.

**Tasks**

- Freeze candidate versions: yaml, sha256, IR, validation report, not-applied report.
- Approve request: only the latest candidate, matching version and sha256; stale approval fails with the current candidate.
- Publisher transaction: verify approver, candidate and hash, re-run L5, set the approval fields on the version, update `campaign_state.active_version_id`. Idempotent retry.
- Single active campaign rule: publishing replaces the active one; define the superseded state.
- Rollback or re-activate a previous version; stop a campaign (deactivate).
- Optional `start_pipeline` flag if U2 says yes.

**Exit criteria**

- Tests: double approve, approve after discard, publish retry, concurrent edits.
- Publisher role can do nothing except the allowed writes.

---

## M8 — MA integration (L6, pickup, status)

**Goal:** prove the MA really accepts and applies what the CA publishes.

**Tasks**

- **[MA repo]** Strict versioned loader, contract package, overlay merge, `--campaign <file>`, effective-config hash in run reports.
- **[MA repo]** Migration grants and pass-start pickup of the active version (`campaign_state.active_version_id`): strict load, sha256 check, last-good fallback, `current_campaign` and supported contract version in status.
- CA: install the MA contract package and a pinned MA checkout on the CA machine; implement L6 as an isolated subprocess (no network, timeout), plus the no-network query-generation check.
- Refuse to publish a version the reported MA contract version cannot load.
- Show whether the MA has applied the active version (read path defined in M0).

**Exit criteria**

- Publish a valid campaign: the MA's `python -m agent leads queries` output changes at the next pass.
- Publish an invalid one: nothing changes and an error is reported.
- MA offline during publish: applied when it returns.

---

## M9 — Web research module

**Goal:** bounded concept research, behind a flag.

**Tasks**

- Query templates for the three closed need types; query policy in code (company names, "list of", "companies using", emails and contact words, supplier words, exclusion matches).
- Search client with allow and deny domains, timeouts, size caps, caching; own SearXNG instance on the CA machine.
- Summarizer (LLM) producing `Suggestion` objects; injection tests on fetched pages.
- Fail-soft behaviour; feature flag off by default.

**Exit criteria**

- Adversarial query-policy tests pass; zero blocked queries reach the search provider.
- Suggestions are always `suggested` and never overwrite confirmed values.

---

## M10 — Evaluation and model selection

**Goal:** choose the smallest model that meets the targets, based on measurements.

**Tasks**

- Build the benchmark: 100–200 labelled descriptions (complete, ambiguous, contradictory, unsupported, adversarial, multi-turn, edits), held-out split.
- Harness: same prompts, schemas, temperature 0, pinned model and quantization, results stored with model digest.
- Measure latency, memory and tokens on the CA machine.
- Compare research on vs off.

**Exit criteria**

- Written model decision with measured numbers.
- Targets in the architecture doc are either met or revised with a reason.

---

## M11 — Deployment and hardening

**Goal:** it runs unattended on the office machine, safely.

**Tasks**

- systemd unit, separate OS user, environment file, log rotation, structured JSON logs (no full prompts or fetched pages at INFO).
- Deploy scripts and a runbook: install, upgrade, rollback, what to do when the model is down or the database is unreachable.
- Security review: untrusted inputs, secret handling, role privileges, resource limits, rate limits per requester.
- Retention policy for campaign text and fetched content (U8).
- Docker or equivalent only if wanted; decide whether `deploy/` files are kept.

**Exit criteria**

- A fresh machine can be set up from the runbook alone.
- Security review findings are fixed or accepted in writing.

---

## M12 — End-to-end acceptance and release

**Goal:** the whole flow works with the dashboard and the MA.

**Tasks**

- End-to-end: dashboard message, questions, edits, approval, MA dry run shows queries reflecting the campaign.
- Stub-model run in CI; real-model nightly run.
- Tag `v0.1.0`; changelog; handover to the dashboard developer.

**Exit criteria**

- Every example campaign in the doc review produces the expected result, including correct "not applied" reports.
- Downstream acceptance of published versions is 100% in the test run.

---

## Open decisions and where they block

| Decision | Blocks | Recommendation |
|---|---|---|
| U1 US-only, size, roles: build in the MA or "not applied" | M1 scope, M3 | "Not applied" in the first release |
| U2 Does approval start the pipeline | M7 | Publish only, optional flag |
| U5 Search provider | M9 | Own SearXNG on the CA machine |
| U7 Model access and CA machine specs | M4, M10 | Own adapter; decide after M10 measurements |
| U8 Retention | M11 | Decide before release |
| A5 Contract ownership and packaging | M8 | Published from the MA repo with a version tag |
| Dashboard access (direct DB role vs thin API) | M0, M6 | Limited DB role |
| MA repo access from the CA machine | M8 | Deploy key, or wait for the contract package |
| Commit attribution | M0 | No Claude co-author (user decision) |

## Not in the first release

Webhooks, version diffs, vocabulary expansion by the model, LLM-worded questions, multiple languages, MA extension keys (include-countries, company size, preferred roles), RBAC, multiple concurrently active campaigns.
