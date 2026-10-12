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

CI runs on every push and pull request (`.github/workflows/`).
