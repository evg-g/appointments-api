# appointments-api — developer tasks.
# Every PR gate has a matching target so the exact same commands run locally and in CI.

.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help setup dev test test-integration test-property test-security lint fix typecheck \
	coverage mutation ci-local clean migrate migrate-down migrate-sql

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install dependencies (uv)
	$(UV) sync --extra dev

dev: ## Run the API locally with reload
	$(UV) run uvicorn appointments_api.main:app --reload --host 0.0.0.0 --port 8000

test: ## Run unit tests with coverage
	$(UV) run pytest tests/unit -q --cov --cov-report=term-missing

test-integration: ## Run integration tests (needs Docker for testcontainers)
	$(UV) run pytest tests/integration -q

test-property: ## Property-based tests: hypothesis + Schemathesis (needs Docker)
	$(UV) run pytest tests/property -q

test-security: ## Security tests: authz matrix, JWT, injection, mass-assignment (needs Docker)
	$(UV) run pytest tests/security -q

coverage: ## Run all tiers under coverage and enforce the services/+api/ gate (needs Docker)
	$(UV) run coverage run -m pytest tests/unit tests/integration tests/security tests/property
	$(UV) run coverage json -o coverage.json
	$(UV) run coverage report
	$(UV) run python scripts/check_coverage.py

mutation: ## Mutation-test services/ with the unit suite and enforce the baseline
	$(UV) run mutmut run
	$(UV) run mutmut export-cicd-stats
	$(UV) run python scripts/check_mutation.py

lint: ## Lint and check formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fix: ## Auto-fix lint issues and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Static type check (strict)
	$(UV) run mypy

migrate: ## Apply all migrations to the database in DATABASE_URL
	$(UV) run alembic upgrade head

migrate-down: ## Roll back the most recent migration
	$(UV) run alembic downgrade -1

migrate-sql: ## Render the full migration as SQL without a database (offline)
	$(UV) run alembic upgrade head --sql

ci-local: lint typecheck coverage ## Run the full PR gate set locally (lint, types, all tiers + coverage gate; needs Docker)

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
