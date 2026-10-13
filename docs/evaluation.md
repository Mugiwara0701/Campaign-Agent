# Evaluation and Model Selection (M10)

Goal: choose the **smallest model that meets the targets**, from measurements. This document describes the benchmark, the
harness and how to decide. **No model has been measured yet**: the numbers must come from the office machine (below), so
the model decision is not written here. `benchmarks/results/` holds the reports once they exist.

## The benchmark

`benchmarks/dataset/cases.jsonl`: 123 labelled cases, built by `benchmarks/build_dataset.py` (deterministic; edit the
script and regenerate, then review the diff). About a quarter of every category is **held out** (`split: heldout`): use
`dev` while tuning prompts and report the final numbers on `heldout` only, once.

| Category | Cases | What it tests |
|---|---|---|
| complete | 32 | Everything needed is said; five sentence shapes, thresholds, excluded companies and countries |
| ambiguous | 20 | Nothing, only an industry, only a technology, only an engineering area: the right questions must follow |
| contradictory | 12 | Qualify score above the draft score, fewer results than pages per query, a target that is also excluded, a size range backwards |
| unsupported | 15 | US only, company size (stored, "not applied"), LinkedIn, approval off, email tone |
| adversarial | 14 | Instructions inside the text, approval switch-off, secrets, hidden characters, another language |
| multi_turn | 10 | A second message that completes or refines the first |
| edit | 20 | Add or remove an exclusion, replace or add a technology, change a threshold, against an existing campaign |

A case is one or more turns. Each turn carries the **gold operations** with the quote that justifies each. A case can also
carry: the starting campaign (`base`), the questions that must be asked at the end (`must_ask`, as gap-engine reasons),
paths that must stay untouched (`forbidden_paths`), the expected outcome of the input guard (`expect_status`), and
research labels (which lookups are expected and which suggested values would be good ones).

Limits of the data: it is synthetic, built from templates over real-looking industries, products and technologies, in
English. It covers the structure of the task, not the vocabulary of real campaigns. **Add each real approved campaign as a
case** (the regression fixtures idea of the architecture document) and re-run.

## What is measured

Scoring is by **outcome**: the values the model added to or removed from the campaign, compared with what the gold
operations change. Wording, order and how many operations it took do not matter.

| Metric | How |
|---|---|
| Schema validity after at most 1 repair | the extractor returned a usable patch (it makes at most one repair call) |
| Field accuracy (precision, recall, F1) | changes per field value, summed over all cases |
| Exact rate; edit correctness | every change right and nothing else changed |
| Unsupported-field generation | an op on `approval.*`, an unknown or system-owned path, or a forbidden path |
| Unsupported detection recall | the unsupported kinds the user asked for were recorded |
| Missing-field recall; conflict detection | the gold gap reasons all appear after the last turn (deterministic gap engine, on what the model extracted) |
| Clarification quality | of the reasons the engine raised, the share that were expected |
| Needless questions | a complete case that still raised a question |
| Guard | input that must be refused was refused |
| Latency (p50, p95), calls, tokens | summed over the attempts of a case |
| Consistency | with `--runs 2` or more: share of cases with identical changes in every run |
| Research | needs asked, suggestions produced and stored, share labelled good, extra latency, **policy violations (must be 0)** |

Memory: the report records what Ollama holds loaded after the run (`/api/ps`) and the machine (CPU, RAM, GPU).
"Targets met" are the proposed targets of section 19 of the architecture document; targets marked "set after baseline"
are reported as a baseline.

**What the benchmark does not measure:** the deterministic parts are exact by construction (the compiler, the validators,
the merger's lock rules); the benchmark only exercises them. Clarification wording is templated, so quality means "the
right question was raised", not "the sentence reads well".

## Running it

Everything runs on the **office machine** (the laptop never runs the model):

```bash
bash deploy/remote.sh deploy
# 1. harness check, no model (also runs in CI):
bash deploy/remote.sh exec ".venv/bin/python -m campaign_agent bench --oracle --split all"
# 2. a model, development split, twice to see consistency:
bash deploy/remote.sh exec ".venv/bin/python -m campaign_agent bench --split dev --runs 2"
# 3. research on and off (needs our own SearXNG, see research.md):
bash deploy/remote.sh exec ".venv/bin/python -m campaign_agent bench --split dev --research both"
# 4. final numbers for the chosen model, once:
bash deploy/remote.sh exec ".venv/bin/python -m campaign_agent bench --split heldout --research both"
```

Reports go to `benchmarks/results/<time>-<model>.md` and `.json` on the office machine; copy the ones you keep into the
repository. To compare models, change `LLM_MODEL` in `.env` (`push-env`, restart is not needed for `bench`) and repeat.
Pin the model tag and quantization; the report stores the digest Ollama reports. Fair comparison rules (same prompts,
schemas, dataset, temperature 0, seed 0, same hardware, same constrained mode, repeated runs) are the defaults of the
harness; `LLM_JSON_MODE` forces a mode so each model can also be measured in its own best mode.

`--oracle` answers every case with its gold patch. It measures no model: it proves the labels agree with the real merger
and the real gap engine, and that the harness and the report work. `tests/unit/test_evaluation.py` runs it on every case.

## Deciding

1. Run every candidate model on `dev` with `--runs 2`. Drop any with schema validity under the target, or with unsupported
   generation above it: those are safety targets, not preferences.
2. Of the rest, take the **smallest** whose missing-field recall, clarification quality and field F1 are within a few points
   of the best. Check latency and memory on the office machine against what the service needs (one request at a time).
3. Run the winner once on `heldout`. Those are the numbers to publish.
4. Write the decision at the end of the report: model tag, quantization and digest, the measured numbers, and which targets
   were revised and why. A target no candidate meets is revised with a reason, never silently.
5. **Research:** compare `off` and `on` on the same cases. Keep it off unless the share of suggestions labelled good is high
   enough to justify the extra latency and the new input surface. Policy violations must be 0 either way.

## Decision record

_Not yet measured. Fill in after step 4: model, digest, numbers, targets met or revised, research on or off._
