# 1. Record architecture decisions

- Status: accepted
- Date: 2026-09-26

## Context

This codebase is meant to be read and learned from. Decisions that are not obvious from the
code (why a constraint lives in the database, why we use testcontainers instead of SQLite,
why services avoid FastAPI imports) need a short written reason, or the "why" is lost.

## Decision

We keep lightweight Architecture Decision Records (ADRs) in `docs/adr/`, numbered
sequentially. Each records **context → decision → consequences** in plain language. One ADR
per decision; superseded ADRs are marked, not deleted.

## Consequences

- A reader can trace why the code looks the way it does.
- Reviewing a change includes reviewing its ADR when one applies.
- The format is deliberately minimal so writing one is cheap.
