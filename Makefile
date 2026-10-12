.PHONY: install lint fix typecheck test audit check db-up db-down

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

db-up:
	docker compose -f deploy/docker-compose.yml up -d --wait
	@echo 'export TEST_DATABASE_URL=postgresql://postgres:dev@127.0.0.1:55432/postgres'

db-down:
	docker compose -f deploy/docker-compose.yml down

audit:
	pip-audit

check: lint typecheck test
