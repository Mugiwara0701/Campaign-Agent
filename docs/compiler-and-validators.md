# Compiler, Serializer and Validators (M3)

Code: `compiler/` (compile, merge, build), `serializer/`, `validators/`. Everything here is deterministic and runs
without a model: IR in, candidate out.

```text
IR → compile_overlay → serialize_overlay → L3 schema → L4 business → L5 YAML → (L6 later) → Candidate
              └→ merge_onto_base → effective config (what the MA would run)
```

`build_candidate(ir)` returns a `Candidate`: the YAML text, its sha256, the IR hash, the effective-config hash, the
contract version, the validation report and the "not applied" list. `can_freeze` is true only when no level failed.
A failing candidate still carries its YAML, so the dashboard can show a preview next to the errors.

## What goes into the overlay

- Only values the user set. `defaulted` values are **not** emitted: the base already supplies them, and pinning a
  default would freeze it against later base changes.
- `search.capabilities` is never emitted. In the MA, `project_context.engineering_requirements` replaces it when set,
  so `audience.capabilities_needed` maps there.
- Fields the contract has no key for (country include list, company size, contact roles, custom signals) are not
  emitted. Each becomes a "not applied" item with the reason and the proposed key. Unsupported requests from the
  IR (LinkedIn, email tone, approval off) are listed too. Nothing is dropped silently.
- The campaign name is reduced to safe characters before it is written.

## Merge semantics (decision)

| Part | Rule |
|---|---|
| Scalars, `project_context` fields | the overlay value replaces the base value |
| Exclusion lists | base first, then the overlay's items; case-insensitive duplicates dropped |
| Anything the overlay leaves out | the base value stays |

An overlay can add exclusions but can never remove one, so "exclusions are not shrunk" holds by construction. L4
checks it anyway as an invariant.

## What the hashes cover (decision)

- `sha256`: the overlay text exactly as stored and sent to the MA. The MA hashes the same bytes it received.
- `effective_hash`: a hash of the merged configuration, for run reports ("this run used campaign X, config Y").
- `ir_hash`: the IR content, also written into the header comment.

The YAML has no timestamp (the version record has the time), so the same IR always gives the same bytes and hash.

## Levels

| Level | Checks | Notes |
|---|---|---|
| L3 schema | types, bounds, patterns, unknown keys, on the overlay and on the effective config | field-level errors only, with the key path |
| L4 business | `min_draft >= min_qualify`; `results_per_query >= pages_per_query`; `max_pages_per_run >= queries x pages`; capabilities from the base list; exclusions not shrunk | rules run on the **merged** values, because a rule can be broken by one overlay key plus a base key |
| L5 yaml | no anchors, aliases, explicit tags, extra documents, duplicate keys, tabs, or comments outside the header; yamllint; round-trip equality | round trip = parse, dump again, compare with the text |
| L6 downstream | the MA's real loader | skipped until M8; shown as `skipped`, not `passed` |

L4 warnings (do not fail the level): job feeds on while hiring signals are off; `accept_service_companies` on (those
firms are competitors); no audience set (the campaign would use only the base vocabulary).

**Fixes.** An issue carries a `fix` only when exactly one deterministic change clears it. Today that is
`pages_starved`: raise `limits.max_pages_per_run` to `queries x pages` if that is within the cap of 300. For
`draft_below_qualify` and `results_below_pages` two changes would work, so there is no fix: that becomes a question
for the user (M5). A fix is shown, never applied silently.

## Changes to M1 made here

- `rule_violations` returns structured violations (code, key, message); `check_rules` still raises, using it.
- `project_context.name` in the overlay accepts safe characters only.
- `yamllint` moved from development to runtime dependencies (L5 uses it).
