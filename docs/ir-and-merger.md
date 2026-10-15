# IR and Patch Merger (M2)

Code: `src/campaign_agent/ir/` (models, patch schemas, provenance, registry, hashing) and
`src/campaign_agent/merger/patch_merger.py`. The merger is pure: it returns a new IR and a report of what was
applied and what was rejected, and never changes its input.

## Who may change what

| Source | Written as | May overwrite | May clear |
|---|---|---|---|
| `extraction` (model reads the user's message) | `inferred`, only with a quote found in the text | unset, defaulted, inferred | no |
| `edit_request` (model reads an edit in words) | `confirmed`, only with a quote found in the text | everything | yes |
| `direct` (dashboard sends path and value) | `confirmed` | everything | yes (`null` clears) |
| `answer` (user answers a question) | `confirmed` | everything | yes |
| web research | pending suggestion only | nothing | no |
| contract defaults | `defaulted` | nothing, only fills unset fields | no |

- The lock is on the whole field: a confirmed list cannot be changed by extraction, even by adding an item.
- A rejected op is reported with a reason code (`locked`, `invalid_value`, `evidence_not_found`, ...). For `locked`,
  the report carries the confirmed value that was kept, so the user can be told.
- `null` from the model means "missing" and is ignored.
- Values are validated strictly against the contract's types and bounds (`"70"` is not an int, `true` is not a number,
  newlines and YAML punctuation in text are refused, roles come from a fixed list).
- Cross-field conflicts (for example `min_draft < min_qualify`) are **not** rejected here. They are a question for
  the user (M5) or a validation error (M3).

## Unsupported and fixed fields

- Unknown paths are never written. They are stored as `unsupported` items so the dashboard can show them.
- `approval.*` is never writable by any source. The attempt is rejected and recorded as an `approval` item. The IR's
  `approval.required` is the constant `true`.
- `meta.id`, `meta.revision`, `meta.contract_version` and `objective.type` are set by the system only.
- Fields the contract cannot express yet (`audience.include_countries`, `employee_min/max`, `contacts.preferred_roles`,
  `audience.signals`) are stored but marked `proposed`; the "not applied" report (M3) lists them.
  `audience.include_countries` is the one exception: it is also written as `search.query_suffix` (the country names,
  added to every search query) unless the user set a suffix. That is a hint, not a filter: the Marketing Agent cannot
  yet reject a company outside the countries, so the report still lists it, with kind `partially_applied`. The strict
  version (`targeting.include_countries`, enforced by the Marketing Agent) is the proposed next step.
- Statements about our own organisation go to `organisation_context` and are never emitted.

## Web suggestions (decision)

The architecture doc allowed two readings of "suggested values". This implementation takes the stricter one:
a suggestion is stored as a pending record and **never changes a field**. The user accepts or rejects it; accepting
writes the value as `confirmed` with the source URLs. Only `audience.industries`, `products`, `technologies` and
`capabilities_needed` accept suggestions. A rejected suggestion is not proposed again. At most 20 are kept.

## Other facts

- Revision goes up by one per merge that changes something; a no-op merge leaves it alone.
- `ir_hash` is a sha256 of the canonical JSON without the revision counter.
- `apply_defaults` fills `qualification.*` and the volume and limit fields from the contract defaults and labels
  them `defaulted`. Blocking fields are the gap engine's concern (M5).
