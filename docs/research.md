# Web Research (M9)

Optional, bounded **concept research**: what a term means, which product categories use a technology, which industries a
capability maps to. It exists so the review can offer "did you also mean ...?" suggestions. It is **off by default**.
Code: `src/campaign_agent/research/`. Wired in `jobs/handlers.py` (`_research`), built in `research/factory.py`.

## What it is not

It never looks for companies, people or contact details, and it never changes a value. The Marketing Agent finds leads;
the Campaign Agent only defines the campaign.

## The pipeline (fixed, not an agent loop)

```text
extractor: research_needs (at most 3, closed kinds, a short term each)
  -> query builder: templates per kind          (the model never writes a query)
  -> query policy: every term and every query   (blocked ones are dropped, with a logged reason)
  -> search: our own SearXNG, filtered by the domain policy
  -> fetch: text only, size and time capped, every redirect hop re-checked
  -> summarizer (one model call per need): pages as delimited data -> a few bounded suggestions
  -> merger.add_suggestions: stored as "suggested", with source URLs
```

Limits per job: **6 searches, 5 page fetches**, at most 2 pages per need, at most 5 suggestions per need.

## Query policy (`query_policy.py`)

A term or query is blocked when it:

- seeks companies: "list of", "top N", "companies", "vendors", "customers of", "competitors", "who uses", "directory";
- seeks people or contacts: email, phone, LinkedIn, address, hiring, jobs, a URL or `@`;
- targets sellers: the Marketing Agent's supplier words (manufacturer, supplier, distributor, buy, price ...);
- looks like an organisation: Inc, Ltd, GmbH, Corp, AG ...;
- matches an exclusion of the campaign (excluded companies, terms, countries), as whole words;
- is not a short plain term (letters, digits and a few symbols, at most 60 characters).

The policy runs on the term, and again on every query built from it, right before the search provider.
**Known limit:** a person's name written as plain words ("John Smith") is not recognised as a person. The terms come
from the model's reading of the user's text, the query templates only ask about meanings, and results are never shown as
leads, so this is accepted; a name list would be a guess.

## Network safety (`search_client.py`, `fetcher.py`)

- Only `http` and `https`; no credentials in the URL; no `localhost`, `.local` or `.internal` names; no IP literal that is
  not public; a hostname must **resolve to public addresses only**.
- A deny list of people and company directories, shops and social networks (`DEFAULT_DENY`), extendable with
  `RESEARCH_DENY_DOMAINS`; `RESEARCH_ALLOW_DOMAINS`, if set, restricts to those domains.
- Redirects are followed by hand, at most 3 hops, and every hop passes the same checks.
- Text content types only; the body is read only up to 200 KB; text is cut to 4,000 characters; timeouts on everything;
  search answers over 500 KB are refused. Results are cached per query for the life of the process.
- **Known limit:** the address is checked before the request, so a DNS answer that changes in between (DNS rebinding) is
  not covered. The machine has no secrets on its network path except the database; run it with no route to internal
  hosts if that matters.

## Pages are hostile (`summarizer.py`)

The page text goes to the model inside `<page>` tags, with the instruction to treat it as data. That is not what protects
us; the **output checks** are. A suggestion is dropped unless:

- its field is one this kind of need may propose (`product_categories_for_technology` only products,
  `industries_for_capability` only industries, `term_meaning` the four concept fields);
- every value is a short plain phrase (at most 6 words, no URL, no email);
- every source URL is a page we actually fetched.

Page text cannot close the delimiters (they are stripped from it). The merger then validates the values again, refuses
fields research may not propose for, and repeats of confirmed values.

## Suggestions never change values

They are stored in `ir.suggestions` with status `suggested` and shown in the review with their sources. A value changes only
when the user accepts one (the existing `suggestion` request), and then it is `confirmed` with the sources. Research does
not touch provenance and does not add questions.

## Fail-soft

Research off, search down, a page that will not load, a model that does not answer, a reply that cannot be read, or any
bug inside the module: the campaign goes on without suggestions. `Researcher.run` never raises. The conversation passes
through `RESEARCHING` only when research is on and the model asked for something.

## The research brief on a proposal (`research/brief.py`)

With research on, every new version that passes validation gets a **research brief** before anyone approves it: what
the target customers look like, each statement with the pages it comes from. It is shown in the proposal as
`review_view.brief` (and by `try`), and it never changes a value of the campaign.

| Part | What it holds |
|---|---|
| `company_types` | kinds of organisations that build or need what the campaign is about |
| `buying_signals` | what shows such an organisation needs outside engineering help |
| `key_terms` | the words they use for it |
| `not_confirmed` | what was asked for but no page that was read supports (and every empty part, by name) |
| `status` | `ready` (all three parts have backed statements), `partial` (some), `unavailable` (none, with the reason) |
| `for_sha256`, `queries`, `searched_at` | the version it belongs to, what was searched, when |

How it is made: subjects from the campaign's own terms (product + technology, industry + technology) → fixed templates
("... product development", "... applications", "... engineering challenges", "... explained") → the same query policy as
above (nothing that seeks companies, people or sellers) → our SearXNG → at most **4 searches and 4 pages**, 2 per query, the first 1,500 characters of each → one model call (pages cited by
number, at most 3 short items per part, a 1,000-token answer) → code checks. A statement is dropped unless it is at most 25 words, has no web or email address and no
legal-form name (Inc, Ltd, GmbH ...), and every source is a page that was fetched. A part left empty is listed under
`not_confirmed`.

One brief per version: it is written when a version is frozen and kept, unchanged, while that version is on screen (an
approval, a refused request or a repeat does not search again). A new version gets a new brief. Any failure (search
down, no page readable, a model that does not answer or answers nonsense, a bug) gives an `unavailable` brief that says
why, and the proposal goes on as before. Measured on the CA machine (CPU-only `qwen3.5:9b`, 2026-10-17): the brief's model call takes about 5-6 minutes; a
whole new proposal (reading the request, then the brief) took 9 minutes. `RESEARCH_BRIEF_TIMEOUT_S` (default 900) is
the brief's own time limit. `RESEARCH_BRIEF=false` keeps research suggestions but turns the brief off.

**Known limits:** the check for company names catches legal-form names and addresses, not a bare brand word; the model
is told not to name companies, and the reviewer sees the sources. The brief describes; it does not prove a market.

## Turning it on

Use **our own** SearXNG instance, on the Campaign Agent machine: `deploy/searxng/` (Docker compose, JSON output, bound
to localhost). It must not be shared with the Marketing Agent, so a CAPTCHA or a block on one does not stop the other.
Docker needs the machine's administrator (the `campaign-agent` account has no sudo), once:

```bash
sudo apt-get install -y docker.io docker-compose-v2
cd /home/<campaign-agent user>/Desktop/Campaign-Agent/deploy/searxng
echo "SEARXNG_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" | sudo tee .env >/dev/null
sudo docker compose up -d
curl -s "http://127.0.0.1:8888/search?q=test&format=json" | head -c 200   # JSON back = it works
```

Then in the Campaign Agent's `.env` (`bash deploy/remote.sh push-env`), and restart it (`bash deploy/remote.sh up`):

```text
RESEARCH_ENABLED=true
SEARXNG_URL=http://127.0.0.1:8888
``` Optional: `RESEARCH_ALLOW_DOMAINS`, `RESEARCH_DENY_DOMAINS`
(comma-separated), `RESEARCH_TIMEOUT_S`.

## Whether it is worth it

Research adds latency (searches, fetches and a model call per need) and a new input that can be manipulated. M10 runs the
benchmark with it on and off; leave it off in production unless that shows suggestions that are accepted often enough to
justify the cost.

## Tests

`tests/unit/test_research.py`: the adversarial policy cases (zero blocked queries reach the provider), the domain and
network rules including redirects to private hosts, hostile page text and manipulated model replies, the budgets, every
fail-soft path, and the handler: suggestions only, values untouched.
