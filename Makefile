.PHONY: install lint fix typecheck test audit check

install:
	pip install -e ".[dev]"
	pre-commit install

lint:
	ruff check .
	ruff format --check .
	yamllint .

fix:
	ruff check --fix .
	ruff format .

typecheck:
	mypy

test:
	pytest --cov

audit:
	pip-audit

check: lint typecheck test
