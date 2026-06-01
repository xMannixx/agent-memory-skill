# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions map to the GitHub milestones in [ROADMAP.md](ROADMAP.md); each entry
links the issues it closed.

## [3.5.0] - 2026-06-01 - Provenance

### Added
- `get_provenance(fact_id, limit=100)`: read-only reconstruction of a fact's
  audit chain in chronological order (write, update, supersede, forget,
  conflict) from the append-only `memory_audit` log. Matches by `fact_id` and
  includes supersede events that reference the id in metadata (`old_id` /
  `new_id`). Unknown ids return an empty list.
- CLI `provenance <fact_id>`: prints one line per event (timestamp, operation,
  source, reason).

### Changed
- Test suite: 148 tests (was 144).

## [3.4.0] - 2026-06-01 - Source Trust

### Added
- Finer source categories: `tool` and `external` join `observation`,
  `conversation`, and `inference`. Trust order (most to least): observation >
  conversation > inference > tool > external.
- Per-lane source-trust matrix: `identity` and `preference` allow observation
  and conversation; `evidence` allows all five; `authorization` allows
  observation only.
- CLI `--source` flag accepts all five source values.

### Security
- `tool` and `external` sources can write only the `evidence` lane; they
  cannot write `identity` or `authorization`. Rejected writes are audited as
  `policy_reject` with reason `source_not_allowed`.

### Changed
- Test suite: 144 tests (was 130).

## [3.3.0] - 2026-06-01 - Neighbor Attributes & Conflict Reconciliation

### Added
- Opt-in neighbor entity attributes on plugin relation lines: when
  `AGENT_MEMORY_BUDGET_ENTITY_ATTRS` is set to N > 0 (default `0` =
  disabled), up to N stored `key=value` pairs per neighbor entity (sorted by
  key) are appended in brackets on each `## Related` line (e.g.
  `- Manni --arbeitet_bei--> Arriva [location=Singen; type=logistics]`).
  Still edge-only; bounded by the existing `relations` section char budget.

### Fixed
- Conflict reconciliation: open `fact_conflicts` rows are automatically marked
  resolved when either referenced fact is no longer active. Runs at the end of
  `consolidate()`, `supersede()`, `forget()`, and `forget_stale()` so
  `stats()["open_conflicts"]` and `get_conflicts()` no longer list ghost
  conflicts for superseded or deleted facts.

### Changed
- Test suite: 130 tests (was 123).

## [3.2.0] - 2026-06-01 - Relation-Aware Plugin Recall

### Added
- Relation-aware plugin recall: on query turns, bounded 1-hop expansion of
  entity relations when the user message mentions known entities (normalized
  term overlap with entity names). Injected under `## Related` (e.g.
  `- Manni --arbeitet_bei--> Arriva`).
- Dedicated `relations` injection budget (default: 6 lines, 1000 characters);
  expands from at most 3 matched entities per turn.
- Opt-out via `AGENT_MEMORY_RELATIONS` (`0` / `false` / `no` / `off` to
  disable). Per-lane override via `AGENT_MEMORY_BUDGET_RELATIONS`.

### Changed
- Plugin injects relation edges only on this path (never facts), so
  authorization content cannot leak through relation recall.
- Test suite: 123 tests (was 119).

## [3.1.0] - 2026-06-01 - Conflict Detection & Entity Relations

### Added
- Conflict detection on single-valued authority lanes (`identity`, `authorization`):
  non-blocking checks on `remember()` when tags are present; `fact_conflicts`
  table; audit events `conflict_detected` and `conflict_resolved`.
- `get_conflicts(include_resolved=False)` and `resolve_conflict(keep_id, drop_ids)`.
- Entity relation graph: `entity_relations` table; `relate()`, `get_relations()`,
  and `related_entities()` (idempotent edges, auto-create entities).
- CLI: `relate`, `relations`, `conflicts` (`--all`), `resolve-conflict`.

### Changed
- `stats()` now includes `open_conflicts` and `relations`.
- `forget_stale_lifecycle()` prunes expired and orphan entity relations.
- CLI `stats` output shows Open Conflicts and Relations.

## [3.0.0] - 2026-06-01 - German-Aware Retrieval

### Added
- German-aware normalization module `text_norm.py`: umlaut folding
  (`ae/oe/ue/ss`), a lightweight attribution-free German suffix stemmer, query
  tokenization, and synonym expansion (#21).
- Editable synonym map `synonyms.json` used to widen recall (#21).
- `_smart_fts_query`: builds an unquoted token-prefix FTS query (`server*`)
  plus synonym expansion so `Server` matches `Serverkonfiguration` and
  `wie laeuft die Infrastruktur` reaches the VPS/Nginx fact (#21).
- Retrieval eval harness `tests/test_retrieval_eval.py` with a self-contained
  fixture (`positives`, `hard_negatives`, `regressions`) measuring recall@3 and
  a precision guard (#23).

### Changed
- Plugin relevance: replaced the binary first-5-characters gate with
  score-based ranking (`_rank_relevant_facts`) that preserves BM25 order and
  never drops candidates; the per-lane budget is the only hard cut (#22).

## [2.1.0] - 2026-06-01 - Hardening & Polish

### Added
- Audit-log retention: `AUDIT_RETENTION_DAYS`, `forget_old_audit()`,
  `audit_rows` in `stats()`, and an `audit-prune` CLI command (#18).
- Plugin import diagnostics: failures are logged with the resolved source path,
  plus a `memory_status()` helper and a CLI `doctor` command (#19).

### Changed
- Standardized all code comments, docstrings, CLI strings, and injected prompt
  headers to English (#20).

## [2.0.0] - 2026-05-30 - Smart Retrieval (local-first)

### Added
- Recall snippet lane: raw conversation snippets stored separately from
  distilled semantic facts, with their own FTS and TTL (#3).
- Query-aware retrieval: later turns retrieve query-relevant evidence instead of
  re-injecting a static baseline (#7).
- Token budgeting: per-lane character limits for injected context, with
  `authorization` facts explicitly never injected (#15).

## [1.4.0] - 2026-05-30 - Consolidation & Decay

### Added
- `consolidate` command to merge related facts into a stronger representative
  and supersede duplicates (#4).
- Exponential confidence decay per authority lane via `half_life_days` (#8).

## [1.3.0] - 2026-05-20 - Audit & Recovery

### Added
- Audit log, database snapshots with restore, and rapid-change write anomaly
  detection (#6).
- Self-observability stats and recall latency counters (#11).

## [1.2.0] - 2026-05-20 - Stability & Hygiene

### Added
- Lesson and entity lifecycle with access tracking and decay on use (#13).
- File-DB coverage and frozen-time test helpers (#16).

### Changed
- Made `remember` idempotent via content-hash IDs (#9).
- Small correctness and hygiene cleanups across the core (#17).

### Performance
- Added facts and lessons indexes and enabled SQLite WAL mode (#12).

### Fixed
- Hardened memory TTL handling and storage cleanup.

## [1.1.0] - 2026-05-06 - Initial release

### Added
- Initial `AgentMemory`: four Authority Lanes (identity / preference / evidence
  / authorization) with per-lane TTL and source policy.
- Rebound-Protection to cap memory intake after idle phases.
- Auto-injection plugin for Hermes and a CLI for managing memory.

[3.5.0]: https://github.com/xMannixx/agent-memory-skill/releases/tag/v3.5.0
[3.4.0]: https://github.com/xMannixx/agent-memory-skill/releases/tag/v3.4.0
[3.3.0]: https://github.com/xMannixx/agent-memory-skill/releases/tag/v3.3.0
[3.2.0]: https://github.com/xMannixx/agent-memory-skill/releases/tag/v3.2.0
[3.1.0]: https://github.com/xMannixx/agent-memory-skill/releases/tag/v3.1.0
[3.0.0]: https://github.com/xMannixx/agent-memory-skill/milestone/6
[2.1.0]: https://github.com/xMannixx/agent-memory-skill/milestone/5
[2.0.0]: https://github.com/xMannixx/agent-memory-skill/milestone/4
[1.4.0]: https://github.com/xMannixx/agent-memory-skill/milestone/3
[1.3.0]: https://github.com/xMannixx/agent-memory-skill/milestone/2
[1.2.0]: https://github.com/xMannixx/agent-memory-skill/milestone/1
