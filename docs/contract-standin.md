# Contract Stand-in

The Campaign Agent's definition of a valid Marketing Agent `leadgen.yaml`, kept in this repo until the MA ships its
own strict loader and contract package (`src/campaign_agent/contract/`). **It is stricter than the MA's loader, which
ignores unknown keys, so "valid here" does not yet prove the MA accepts a file.** That proof is level L6 (milestone M8).

- Base config: verbatim copy of `config/leadgen.yaml` from `Marketing-AI-Agent` at commit
  `79a29d8d6ca7` (sha256 checked by a test).
- Two models: `LeadgenFile` (the whole file) and `CampaignOverlay` (only what the CA may emit).
- Strict: unknown or misspelled keys, wrong types (`"16"` is not an int), out-of-range values and duplicate YAML
  keys are all rejected, with the key path.
- Rules checked: `min_draft >= min_qualify`, `max_pages_per_run >= queries_per_run x pages_per_query`,
  `results_per_query >= pages_per_query`. On an overlay only the rules whose keys are all present run; the full check
  runs on the merged config (M3).
- Bounds are **proposed** limits chosen around the base values, not MA limits. Confirm them (M0 decision).
- Defaults and bounds are declared once, in the models. This table is generated from them.

## Findings from tracing the MA code

- `project_context` values replace `search.products`, `search.industries` and `search.capabilities` in the MA
  (`strategy._vocab`). The overlay therefore sets audience through `project_context` only.
- `limits.host_delay_seconds` is read by no code: accepted in the full file, never emitted.
- `accept_service_companies` defaults to `True` in `qualify.py`/`pipeline.py` and `False` elsewhere and in the file.
  The contract uses `False`.
- Several MA inline defaults differ from the file (right-hand column).

## Keys

Owner: `campaign` = the CA may set it; `base` = kept in the base config; `infra`/`dead` = never emitted.

| Key | Owner | Contract default | Bounds | MA inline default | MA readers |
|---|---|---|---|---|---|
| `config_version` | campaign | None |  |  | - |
| `search.queries_per_run` | campaign | 16 | ge 1, le 40 | `12` | strategy.py |
| `search.repeat_after_days` | campaign | 14 | ge 0, le 90 | `14` | strategy.py |
| `search.results_per_query` | campaign | 15 | ge 1, le 30 | `10` | pipeline.py |
| `search.use_job_feeds` | campaign | False |  | `False` | pipeline.py |
| `search.feed_items` | campaign | 40 | ge 1, le 100 | `40` | pipeline.py |
| `search.pages_per_query` | campaign | 6 | ge 1, le 10 | `4` | pipeline.py |
| `search.query_suffix` | campaign |  |  | `` | pipeline.py |
| `search.capabilities` | campaign | required |  |  | strategy.py |
| `search.products` | base | required |  |  | strategy.py |
| `search.industries` | base | required |  |  | strategy.py |
| `search.platforms` | base | required |  |  | strategy.py |
| `search.builder_phrases` | base | required |  |  | strategy.py |
| `search.need_phrases` | base | required |  |  | strategy.py |
| `search.rfp_words` | base | required |  |  | strategy.py |
| `project_context.name` | campaign |  |  |  | strategy.py, service.py |
| `project_context.industries` | campaign | [] |  |  | strategy.py |
| `project_context.products` | campaign | [] |  |  | strategy.py |
| `project_context.technologies` | campaign | [] |  |  | strategy.py |
| `project_context.hardware` | base | [] |  |  | strategy.py |
| `project_context.engineering_requirements` | campaign | [] |  |  | strategy.py |
| `qualification.min_qualify` | campaign | 50 | ge 0, le 100 | `50` | pipeline.py, qualify.py |
| `qualification.min_draft` | campaign | 60 | ge 0, le 100 | `60` | pipeline.py |
| `qualification.min_confidence` | campaign | 0.5 | ge 0, le 1 | `0.5` | qualify.py |
| `qualification.accept_hiring_signals` | campaign | True |  | `True` | qualify.py, strategy.py |
| `qualification.accept_service_companies` | campaign | False |  | `True in qualify.py/pipeline.py, False in contacts.py/strategy.py` | qualify.py, pipeline.py, contacts.py, strategy.py |
| `qualification.max_age_days` | campaign | 120 | ge 1, le 730 | `120` | qualify.py |
| `qualification.min_relevance_terms` | campaign | 2 | ge 0, le 10 | `2` | qualify.py |
| `exclude_terms` | campaign | [] |  |  | pipeline.py, qualify.py |
| `exclude_companies` | campaign | [] |  |  | pipeline.py, qualify.py |
| `exclude_countries` | campaign | [] |  |  | pipeline.py, qualify.py |
| `exclude_tlds` | campaign | [] |  |  | pipeline.py, qualify.py |
| `contacts.max_pages` | base | 4 | ge 1, le 8 |  | contacts.py |
| `contacts.max_attempts` | base | 3 | ge 1, le 5 |  | pipeline.py |
| `contacts.link_labels` | base | required |  |  | contacts.py |
| `limits.max_new_leads_per_run` | campaign | 10 | ge 1, le 50 | `10` | pipeline.py |
| `limits.max_pages_per_run` | campaign | 100 | ge 1, le 300 | `60` | browser/__init__.py |
| `limits.max_pages_per_domain` | campaign | 6 | ge 1, le 10 | `6` | browser/__init__.py |
| `limits.host_delay_seconds` | dead | 2.0 | ge 0, le 60 |  | - |
| `limits.engine_rest_minutes` | campaign | 30 | ge 1, le 240 | `30` | browser/__init__.py |
| `desktop_engines` | infra | ['default'] |  |  | browser/__init__.py, config.py |
