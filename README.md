# Campaign Agent

Turns a plain-language campaign description into a validated, versioned campaign
configuration for the Marketing Agent. See `docs/` for the design.

## Development

Python 3.12 or newer.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make install
make check      # lint, type check, tests
make fix        # auto-fix lint and formatting
```

The database tests need a throwaway local PostgreSQL (they rebuild its schema, so they refuse any other host):

```bash
make db-up
export TEST_DATABASE_URL=postgresql://postgres:dev@127.0.0.1:55432/postgres
pytest
```

Without `TEST_DATABASE_URL` those tests are skipped. Run the service with `python -m campaign_agent start`.

CI runs on every push and pull request (`.github/workflows/`).
