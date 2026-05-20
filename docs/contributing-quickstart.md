# Contributing Quickstart

This project uses GitHub issues and milestones to track memory-system work. The goal is to keep each issue scoped enough to review, test, and ship independently.

## Label Scheme

Labels are split into three axes:

- `tier:*` explains priority and product importance.
- `area:*` explains which part of the system is affected.
- `type:*` explains the kind of change.

Use at least one label from each axis when opening implementation issues.

## Priority Tiers

| Label | Meaning |
|-------|---------|
| `tier:must-do` | Correctness, data integrity, security, or stability work that should land early. |
| `tier:high-value` | Important improvements with clear practical payoff. |
| `tier:exploratory` | Larger architecture or retrieval work that needs design validation first. |
| `tier:polish` | Maintainability, test coverage, scale, documentation, and usability cleanup. |

## Areas

| Label | Scope |
|-------|-------|
| `area:core` | `AgentMemory`, facts, lessons, entities, policy behavior. |
| `area:plugin` | Hermes plugin and context injection behavior. |
| `area:cli` | `fact.py` CLI behavior. |
| `area:schema` | SQLite schema, migrations, indexes, and persistence. |
| `area:search` | FTS, semantic search, hybrid ranking, retrieval quality. |
| `area:security` | Memory poisoning, prompt injection, audit, snapshots, rollback. |
| `area:tests` | Fixtures, property tests, time controls, migration tests. |
| `area:docs` | Roadmap, README, references, issue templates. |

## Workflow

1. Start with an issue. Keep the issue focused on one behavior or design decision.
2. Use the milestone to decide release order.
3. Keep pull requests small and tied to one issue where possible.
4. Include tests when changing memory behavior, schema, or policy rules.
5. Document migration behavior whenever existing SQLite data may be affected.

## Issue Quality Checklist

- The problem is clear.
- Scope and out-of-scope are explicit.
- Acceptance criteria are testable.
- Data compatibility risk is called out.
- Security-sensitive behavior has a negative test where possible.
