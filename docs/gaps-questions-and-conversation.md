# Gap Engine, Questions and Conversation State (M5)

Code: `gap/engine.py` (what is wrong or missing), `questions/` (templated questions and what answers do),
`conversation/` (state machine, remembered state, one planned turn). Nothing here calls a model.

```text
IR changed → plan_turn: analyze → auto-fix → close stale questions → ask new ones (max 3 open)
                         └ event: GAPS_FOUND (a question is open) or COMPLETE → state machine
```

## Findings, most urgent first

| Kind | What | Question |
|---|---|---|
| conflict | `min_draft < min_qualify`; `results_per_query < pages_per_query`; page budget too small for the queries; employee range reversed; the same thing both targeted and excluded (quotes both statements) | choose which side changes; **cannot be skipped** |
| drift | a product, industry, technology, capability or query suffix with a supplier word (`manufacturer`, `maker`, `OEM`, `price` ...) | remove it or name what they build; **cannot be skipped** |
| unclear | a value the merger rejected as out of range, or (for roles, countries, company size only) not supported by its quote | ask again; roles are a fixed list |
| gap | nothing about the audience at all | which industries? which engineering areas? |
| ambiguity | an engineering area but no industry or product ("embedded engineers"); an industry but no area ("automotive"); a technology but no product ("Android") | ask the missing half |
| preference | an unscoped statement ("we prefer small companies") | this campaign only, or all campaigns? |

Supplier wording matters because the MA raises an error in the middle of a run for any query containing such a word.
The same rule is also an L4 error, so a campaign with it can never be frozen.

An ungrounded value on an ordinary field (for example the invented `engine_rest_minutes` seen in the real-model run)
is a model slip, not a request, and is not turned into a question.

## Rules for questions

- Questions are templated; no model writes them. Choices come from the base vocabulary or from computed values.
- **At most 3 open at a time.** New ones fill the free slots.
- **A question is never asked twice.** Its key is `path|reason|instance`; the instance carries the values of a
  conflict, so the same conflict with new values is a new situation. Answered and skipped keys are remembered.
- **Skip** takes the default and is allowed for gaps, ambiguities, unclear values and preferences only.
- An answer is the user's own change: it goes through the merger as `confirmed`. Free text goes through the input
  guard (secrets refused) and the contract's validation; a refused answer leaves the question open.
- Open questions whose situation disappeared (the user fixed it another way) are closed automatically.

## Automatic fix

One fix is made without asking: a page budget too small for the queries is raised to `queries x pages` when that is
within the cap. It is written as `inferred`, shown to the user as a notice, and never overrides a confirmed value;
if the user confirmed the budget, it becomes a question instead.

## State machine

38 transitions plus discard, in `conversation/state_machine.py`; everything else raises `IllegalTransitionError`.
The M0 corrections:

- **Discard** works from every state except `PUBLISHED` and `DISCARDED`. A never-published campaign becomes
  `DISCARDED`; one with a published version only drops its pending change and returns to `PUBLISHED`, so the live
  campaign is untouched.
- **A failed final check at approval** (level 5 or 6) moves to `VALIDATION_FAILED`. `PUBLISH_FAILED` is for store
  failures only, and retrying from it is safe.
- **Suggestions:** accepting one changes a value, so it needs a new validation; rejecting one changes nothing.
- Direct field edits still work while the model is unavailable.
- The state names are checked against the database constraint in `migrations/0013_campaign_agent_tables.sql`.

## Other changes in this milestone

- Merger: a `SYSTEM` source for deterministic fixes (written as `inferred`, no quote, never over a confirmed value).
- L4: `supplier_wording` error (see above).
- Extractor prompt: a general preference goes to `organisation_context`.
