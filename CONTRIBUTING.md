# Contributing to appointments-api

## Setup

```bash
# WSL (Ubuntu-24.04)
make setup
```

This creates a `.venv` with [uv](https://docs.astral.sh/uv/) and installs the project with
its `dev` extras. `uv.lock` is committed; use `uv sync` to reproduce it exactly.

## Before you push

Run the same gates CI runs:

```bash
# WSL (Ubuntu-24.04)
make ci-local
```

That runs, in order: `ruff` (lint + format check), `mypy --strict`, and the test suite
with coverage. If it passes locally, it passes in CI.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`test:`, `ci:`, `docs:`, `refactor:`, `chore:`. Keep commits small and focused — one
logical change each.

## Tests

Every change ships with tests. See `docs/TESTING.md` (added in a later milestone) for the
test pyramid. Rules: AAA structure, one behaviour per test, descriptive names, no `sleep()`,
no order dependence between tests.

## Decisions

Any non-obvious choice gets a short ADR in `docs/adr/NNNN-title.md` (context → decision →
consequences).
