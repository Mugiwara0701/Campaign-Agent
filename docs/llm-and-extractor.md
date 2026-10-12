# LLM Adapter, Input Guard and Extractor (M4)

Code: `llm/` (adapter interface, Ollama and OpenAI-compatible backends, scripted stub), `guard/`, `extractor/`,
`settings.py`.

```text
user text → L1 input guard → extractor (1 model call) → IRPatch → merger (M2) → IR
                 │                  └ repair · retry · step-down · fallback, all inside one call budget
                 └ rejected text never reaches the model
```

## Settings

Read from the environment or a git-ignored `.env` (unknown keys are ignored; secrets are never printed).

| Key | Default | Meaning |
|---|---|---|
| `LLM_BACKEND` | `ollama` | `ollama` or `openai` (vLLM, hosted) |
| `LLM_BASE_URL` | `http://127.0.0.1:11434` | |
| `LLM_API_KEY` | empty | only for `openai` |
| `LLM_MODEL` | `qwen3.5:9b` | |
| `LLM_TIMEOUT_S` | 300 | per call |
| `LLM_MAX_CONTEXT` | 8192 | at least 2048: the extraction prompt alone is about 1,300 tokens |
| `LLM_JSON_MODE` | `auto` | `auto` uses the strongest mode the backend supports; or force `schema`, `json`, `prompt` |
| `LLM_THINK` | false | thinking models answer faster and more reliably without it for this task |

## Output modes

Strongest first: **schema** (the backend constrains output to the JSON schema), **json** (valid JSON only),
**prompt** (the prompt asks for JSON and the reply is parsed strictly). The schema is derived from the IR patch models:
paths are an enum of the registry's patchable paths (`approval` is not in it) and values are a flat union.
Whatever the mode, the reply is validated against the patch schema.

## Input guard (L1)

Rejects, before anything else sees the text: empty, longer than 5,000 characters (2,000 for an edit), control or
invisible characters, mostly non-Latin text, and secret-like strings (private keys, AWS, GitHub, Slack and Google keys,
`sk-` keys, JWTs, URLs with passwords, `password: ...` assignments). The issue names the kind, never the secret. The
cleaned text is what the model, the evidence check and the stored turn all use.

## Extractor and failure handling

One call per turn. Budget: **6 calls per job, counting retries, repairs and step-downs**.

| Situation | Behaviour |
|---|---|
| Timeout, connection error, 5xx | one retry, then `model_unavailable` (draft kept) |
| Backend refuses the mode | step down schema → json → prompt |
| Not JSON, or not matching the schema | one repair call quoting the error, then one call to the fallback model if configured, then `needs_rephrase` |
| Budget used up | `budget_exhausted` |
| Prompt too large | the current campaign is dropped from an edit prompt (warning); the user's text never is; if it still does not fit, `too_long` |

The user text goes in `<user_text>` tags as data; a closing tag inside the text is defused. A reply may carry a
`<think>` block or one code fence; anything else around the JSON is invalid. The model has no tools.

## Two checks found with a real model

Run against a local `qwen3.5:9b` (Ollama, schema mode, about 28 s per call on this laptop), the model got most of an
example campaign right but made two mistakes, which are now handled:

1. **A real quote used for an invented value.** It set `limits.engine_rest_minutes = 60` citing "Also message them on
   LinkedIn". The quote exists, so the evidence check passed it. The merger now also checks that the **value is
   supported by the quote**: numbers must appear in it (commas in `5,000` ignored; 0.7 may be written 70%), and each
   word of a value must share its first five letters with a word of the quote. Number words ("five hundred") are
   rejected rather than guessed. Booleans cannot be checked. User sources (direct edit, answer) need no grounding.
2. **`add` on a single value.** The extractor rewrites that to `set`; it is unambiguous.

The same run proposed six contact roles for "find the right decision makers"; none is named in the quote, so all were
rejected. That request is currently lost from the user's point of view; the gap engine (M5) should turn a rejected
`contacts.preferred_roles` into a question ("which roles?").

This is one run, not a benchmark. Model quality is measured in M10.

## Testing

All of the above runs in CI against `StubAdapter`, with `httpx.MockTransport` for the two backends. The live test
(`tests/integration/test_live_ollama.py`) runs only with `LLM_LIVE=1`.
