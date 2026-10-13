"""A stand-in for the Marketing Agent, for the end-to-end tests only.

It implements the one command the Campaign Agent relies on (docs/marketing-agent-m8-work.md):

    python -m agent leads queries --campaign <overlay.yaml>

It loads the overlay with the contract's strict loader, merges it onto the base config, validates the result, and prints
search queries built from the campaign. Exit code 2 with one readable line on stderr when the file is rejected. It is NOT
the real Marketing Agent: the real one has to pass the same tests before the Campaign Agent goes into production.
"""
