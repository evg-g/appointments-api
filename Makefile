# appointments-api — developer tasks.
# Every PR gate has a matching target so the exact same commands run locally and in CI.

.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help setup dev test test-integration lint fix typecheck ci-local clean

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

lint: ## Lint and check formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fix: ## Auto-fix lint issues and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Static type check (strict)
	$(UV) run mypy

ci-local: lint typecheck test ## Run the full PR gate set locally

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
