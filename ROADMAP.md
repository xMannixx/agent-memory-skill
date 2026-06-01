# Agent Memory Roadmap

`agent-memory-skill` is a small, local-first memory layer for Hermes Agent. The next phase is about turning the current prototype into a durable memory subsystem: safer storage, better retrieval, clearer lifecycle rules, and security controls against memory poisoning.

The roadmap is organized by release milestones. Each item is tracked as a GitHub issue with labels for priority tier, area, and change type.

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| [v1.2 - Stability & Hygiene](https://github.com/xMannixx/agent-memory-skill/milestone/1) | Make the current SQLite memory core predictable, indexed, testable, and easier to maintain. | Complete |
| [v1.3 - Audit & Recovery](https://github.com/xMannixx/agent-memory-skill/milestone/2) | Add auditability, rollback, anomaly detection, and operational stats. | Complete |
| [v1.4 - Consolidation & Decay](https://github.com/xMannixx/agent-memory-skill/milestone/3) | Move from simple retention windows toward consolidation and confidence decay. | Complete |
| [v2.0 - Smart Retrieval](https://github.com/xMannixx/agent-memory-skill/milestone/4) | Add richer retrieval paths: recall memory, smarter plugin injection. | Complete |
| [v2.1 - Hardening & Polish](https://github.com/xMannixx/agent-memory-skill/milestone/5) | Reliability and publishability: audit-log retention, plugin diagnostics, English code/CLI/prompt standardization. | Complete |
| [v3.0 - German-Aware Retrieval](https://github.com/xMannixx/agent-memory-skill/milestone/6) | Deterministic German-aware retrieval: token-prefix FTS + synonyms, fold/stem scoring (boost not gate), and a measurable eval harness. | Complete |
| v3.1 - Conflict & Relations | Non-blocking conflict detection on single-valued authority lanes; lightweight entity relation graph (stdlib-only). | Complete |
| v3.2 - Relation-Aware Recall | 1-hop relation expansion in the plugin on query turns (edge-only, budgeted). | Complete |
| v3.3 - Neighbor Attributes | Opt-in neighbor attribute injection on relation lines; conflict auto-reconciliation when referenced facts become inactive. | Complete |
| v3.4 - Source Trust | Finer source categories (`tool`, `external`) and per-lane source matrix; lower-trust input quarantined to `evidence`. | Complete |
| v3.5 - Provenance | Read-only provenance view derived from the append-only audit log; `get_provenance()` and CLI `provenance` command. | Complete |
| v3.6 - Procedural Lane | Fifth authority class for self-written behavioral rules: observation-only writes, a mandatory human review-gate (no auto-approve), deterministic rule-conflict / artifact-bloat detection, and bounded, query-aware prompt injection. | Complete |

## Priority Tiers

| Tier | Meaning |
|------|---------|
| `tier:must-do` | Fixes or hardening work that protects correctness, data integrity, or security. |
| `tier:high-value` | Conceptually important improvements with a strong practical payoff. |
| `tier:exploratory` | Larger retrieval or architecture upgrades that need careful design before implementation. |
| `tier:polish` | Maintainability, scale, testing, and usability improvements. |

## Planned Work

| # | Issue | Milestone | Tier | Area | Status |
|---|-------|-----------|------|------|--------|
| 1 | [#3 `feat(core): introduce recall lane for raw conversation snippets`](https://github.com/xMannixx/agent-memory-skill/issues/3) | v2.0 | `tier:exploratory` | `area:core`, `area:schema` | Closed |
| 2 | [#4 `feat(cli): consolidate command to merge related facts`](https://github.com/xMannixx/agent-memory-skill/issues/4) | v1.4 | `tier:high-value` | `area:core`, `area:cli` | Closed |
| 3 | [#5 `feat(search): hybrid FTS5 + sqlite-vec retrieval with RRF`](https://github.com/xMannixx/agent-memory-skill/issues/5) | v2.0 | `tier:exploratory` | `area:search` | Closed (not planned) |
| 4 | [#6 `feat(security): audit log, snapshots and rapid-change anomaly detection`](https://github.com/xMannixx/agent-memory-skill/issues/6) | v1.3 | `tier:must-do` | `area:security`, `area:schema` | Closed |
| 5 | [#7 `feat(plugin): query-aware retrieval at second turn onward`](https://github.com/xMannixx/agent-memory-skill/issues/7) | v2.0 | `tier:exploratory` | `area:plugin` | Closed |
| 6 | [#8 `feat(core): exponential confidence decay per authority lane`](https://github.com/xMannixx/agent-memory-skill/issues/8) | v1.4 | `tier:high-value` | `area:core`, `area:schema` | Closed |
| 7 | [#9 `fix(core): make remember idempotent via content-hash IDs`](https://github.com/xMannixx/agent-memory-skill/issues/9) | v1.2 | `tier:must-do` | `area:core` | Closed |
| 8 | [#10 `feat(schema): namespaces for multi-user and multi-agent memory`](https://github.com/xMannixx/agent-memory-skill/issues/10) | Backlog | `tier:polish` | `area:schema` | Open |
| 22 | [#30 `feat(core): procedural authority lane with mandatory review-gate and rule-conflict detection`](https://github.com/xMannixx/agent-memory-skill/issues/30) | v3.6 | `tier:exploratory` | `area:core`, `area:security` | Shipped (lean MVP; synthesized from the swarm RFC) |
| 9 | [#11 `feat(core): self-observability stats and latency counters`](https://github.com/xMannixx/agent-memory-skill/issues/11) | v1.3 | `tier:polish` | `area:core`, `area:cli` | Closed |
| 10 | [#12 `perf(schema): add facts and lessons indexes and enable WAL`](https://github.com/xMannixx/agent-memory-skill/issues/12) | v1.2 | `tier:must-do` | `area:schema` | Closed |
| 11 | [#13 `feat(core): lesson and entity lifecycle with decay on use`](https://github.com/xMannixx/agent-memory-skill/issues/13) | v1.2 | `tier:polish` | `area:core`, `area:schema` | Closed |
| 12 | [#14 `feat(core): finer source trust graduation and promotion rules`](https://github.com/xMannixx/agent-memory-skill/issues/14) | v3.4 | `tier:polish` | `area:core` | Closed (scoped; promotion rules dropped) |
| 13 | [#15 `feat(plugin): token budget, lazy tool retrieval, authorization lane decision`](https://github.com/xMannixx/agent-memory-skill/issues/15) | v2.0 | `tier:exploratory` | `area:plugin` | Closed |
| 14 | [#16 `test(infra): file-DB coverage, property tests, frozen-time helpers`](https://github.com/xMannixx/agent-memory-skill/issues/16) | v1.2 | `tier:polish` | `area:tests` | Closed |
| 15 | [#17 `refactor(core): small hygiene umbrella`](https://github.com/xMannixx/agent-memory-skill/issues/17) | v1.2 | `tier:polish` | `area:core` | Closed |
| 16 | [#18 `feat(core): audit-log retention and pruning`](https://github.com/xMannixx/agent-memory-skill/issues/18) | v2.1 | `tier:polish` | `area:core`, `area:schema` | Closed |
| 17 | [#19 `feat(plugin): import diagnostics and doctor command`](https://github.com/xMannixx/agent-memory-skill/issues/19) | v2.1 | `tier:polish` | `area:plugin`, `area:cli` | Closed |
| 18 | [#20 `docs(core): standardize code, CLI, and prompt language to English`](https://github.com/xMannixx/agent-memory-skill/issues/20) | v2.1 | `tier:polish` | `area:docs`, `area:core` | Closed |
| 19 | [#21 `feat(core): German-aware normalization and token-prefix FTS query`](https://github.com/xMannixx/agent-memory-skill/issues/21) | v3.0 | `tier:must-do` | `area:search`, `area:core` | Closed |
| 20 | [#22 `feat(plugin): score-based relevance ranking instead of binary filter`](https://github.com/xMannixx/agent-memory-skill/issues/22) | v3.0 | `tier:must-do` | `area:plugin`, `area:search` | Closed |
| 21 | [#23 `test(search): retrieval eval harness with hard negatives and regressions`](https://github.com/xMannixx/agent-memory-skill/issues/23) | v3.0 | `tier:must-do` | `area:search`, `area:tests` | Closed |

## Release Themes

### v1.2 - Stability & Hygiene

This release keeps behavior close to the current implementation while making it safer to grow. The focus is idempotent writes, schema indexes, WAL mode, lifecycle coverage for lessons/entities, file-backed tests, and small hygiene fixes.

### v1.3 - Audit & Recovery

Persistent memory needs a forensic trail. This milestone adds audit logging, snapshots, rollback, rapid-change anomaly detection, and basic observability counters. The security direction is informed by OWASP Agent Memory Guard and prompt-injection prevention guidance.

### v1.4 - Consolidation & Decay

This milestone moves beyond hard TTLs. The goal is to consolidate repeated evidence into stronger facts and introduce authority-lane-specific confidence decay. It is inspired by current work on reflective, episodic, and semantic memory systems.

### v2.0 - Smart Retrieval

This is the architectural retrieval milestone, and it shipped local-first: raw conversation snippets live in a separate recall lane, plugin prompt context is budgeted, authorization memory is explicitly not injected, and later turns can retrieve query-relevant evidence. Semantic/vector retrieval (sqlite-vec) was evaluated and deliberately dropped (#5, closed as not planned): the project stays stdlib-only and dependency-free, and the v3.0 German-aware retrieval covers the synonym/concept gap deterministically.

### v2.1 - Hardening & Polish

A reliability and publishability pass. Audit logging gained a retention window and a prune path so it no longer grows unbounded. The plugin now logs import and instantiation failures (instead of silently injecting nothing) and ships a `doctor` command and a `memory_status()` helper for troubleshooting. All German docstrings, comments, CLI strings, and injected prompt headers were standardized to English.

### v3.0 - German-Aware Retrieval

Retrieval became German-aware while staying fully deterministic and local (no embeddings, no LLM calls). The FTS query is built from unquoted token-prefix terms plus a synonym map, so `Server` matches `Serverkonfiguration` and `wie laeuft die Infrastruktur` reaches the VPS/Nginx fact. A lightweight, attribution-free German stemmer and umlaut folding (ae/oe/ue/ss) drive relevance scoring. The plugin replaced its binary relevance gate with a score-based ranking that no longer drops good BM25 hits. A self-contained eval harness (positives + hard negatives + regressions) makes every retrieval change measurable.

### v3.1 - Conflict & Relations

Two stdlib-only capabilities shipped without changing the dependency profile:

1. **Conflict detection** — `single_valued` policy on authority lanes; on `remember()`, detect active facts in the same lane with identical tags but different content; record in `fact_conflicts`, audit `conflict_detected`, resolve via `get_conflicts()` / `resolve_conflict()`; `stats()` exposes `open_conflicts`. Non-blocking at write time; complements automatic `consolidate()` for same-(lane, tags) groups.
2. **Entity relations** — `entity_relations` table and `relate()` / `get_relations()` / `related_entities()` APIs; lifecycle cleanup in `forget_stale_lifecycle()`; `stats()` exposes `relations`.

Deferred items: procedural memory and multi-agent namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

### v3.2 - Relation-Aware Recall

The Hermes auto-injection plugin now performs bounded 1-hop expansion of entity
relations on query turns: when the user message mentions known entities, their
direct relations are injected under `## Related` (edges only — no facts, so no
authorization leak). A dedicated `relations` budget applies (default 6 lines /
1000 characters); at most 3 matched entities are expanded per turn. Opt out via
`AGENT_MEMORY_RELATIONS`; override limits with `AGENT_MEMORY_BUDGET_RELATIONS`.

This makes the v3.1 entity graph useful at recall time instead of only through
the CLI. Deferred items: procedural memory and multi-agent namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

### v3.3 - Neighbor Attributes & Conflict Reconciliation

Two focused improvements shipped without changing the dependency profile:

1. **Neighbor attribute injection (opt-in)** — On query turns, when
   `AGENT_MEMORY_BUDGET_ENTITY_ATTRS` is set to N > 0 (default `0` =
   disabled), up to N stored `key=value` attributes per neighbor entity (sorted
   by key) are appended to each injected relation line in `## Related`, e.g.
   `- Manni --arbeitet_bei--> Arriva [location=Singen; type=logistics]`.
   Still edge-only (no facts); output remains bounded by the `relations`
   section budget.
2. **Conflict reconciliation** — Open `fact_conflicts` rows are automatically
   marked resolved when either referenced fact is no longer active. Runs at the
   end of `consolidate()`, `supersede()`, `forget()`, and `forget_stale()` so
   `open_conflicts` and `get_conflicts()` do not show ghost pairs after
   supersede or delete.

Deferred items: relevance ranking beyond current score-based ordering,
alias/coreference recognition, dynamic per-turn budget, procedural memory, and
multi-agent namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

### v3.4 - Source Trust

Memory poisoning defenses gained finer source categories and a per-lane write
matrix:

1. **Five sources** — `observation`, `conversation`, `inference`, `tool`,
   `external` (trust order: observation > conversation > inference > tool >
   external; `external` = untrusted, e.g. external documents).
2. **Per-lane policy** — `identity` and `preference`: observation,
   conversation only; `evidence`: all five (quarantine lane); `authorization`:
   observation only.
3. **Guarantee** — `tool` and `external` can write only `evidence`; they
   cannot write `identity` or `authorization`. Rejected writes audit as
   `policy_reject` / `source_not_allowed`. CLI `--source` accepts all five
   values.

Promotion / repeated-verification rules were intentionally out of scope
(overlap with existing `consolidate()` confidence behavior). Deferred items:
procedural memory and multi-agent namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

### v3.5 - Provenance

A read-only provenance view reconstructs a fact's history from the existing
append-only audit log (`memory_audit`). No new table or column was added — the
audit log remains the single source of truth.

1. **`get_provenance(fact_id, limit=100)`** — Returns the fact's audit chain in
   chronological order (oldest first): write, update, supersede, forget, and
   conflict events. Matches rows by `fact_id` and includes supersede events
   that reference the id in metadata (`old_id` / `new_id`), so a superseded
   fact shows it was superseded and a keeper shows what it superseded. Unknown
   ids return an empty list.
2. **CLI `provenance <fact_id>`** — Prints one line per event (timestamp,
   operation, source, reason).
3. **Design note** — Provenance is derived from the audit log; there is
   intentionally no duplicate `provenance_chain` storage.

Deferred items (now partly addressed by v3.6): async consolidation;
multi-factor retrieval ranking; OpenTelemetry observability; and multi-agent
namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

### v3.6 - Procedural Lane

A fifth authority class, `procedural`, stores self-written **behavioral rules**
(how the agent should respond) separately from facts (what is true). Rules are
self-modifying behavior code, so they get a review-gate, conflict detection,
bounded injection, and expiry — not just a confidence score. The design
synthesizes a multi-model "swarm" RFC; the brief and the comparison of the
proposals live in [docs/briefs/procedural-lane-rfc.md](docs/briefs/procedural-lane-rfc.md)
and the `procedural-lane-rfc-comparison` canvas. This is a deliberately lean MVP
of that design.

1. **Observation-only writes, never auto-active** — `propose_rule()` accepts
   rules only from `source="observation"` (the agent can surface candidates but
   cannot write free-form self-instructions into the prompt). Every rule starts
   `pending`. `approve_rule()` is a mandatory human gate: there is no
   auto-approval regardless of confidence. `remember(authority_class="procedural")`
   is rejected and routed here (`policy_reject` / `use_procedural_lane`).
2. **Deterministic conflict detection (stdlib-only)** — On approval, a candidate
   is checked against active rules using trigger-overlap plus a structured
   effect vector. **Direct contradictions** (opposite values on the same effect
   dimension with overlapping triggers) hard-block activation and cannot be
   overridden. **Interactions** (same domain) and **artifact bloat** (cumulative
   `artifact_cost` over budget — the dominant real-world drift class) soft-block
   unless explicitly acknowledged. Conflicts are recorded in `rule_conflicts`
   and audited.
3. **Drift containment** — Per-domain budgets (`PROCEDURAL_DOMAIN_BUDGET`), a
   global active-rule cap, a 30-day TTL with re-approval, and supersession via
   `previous_rule_id` keep accumulation visible and bounded. `forget_stale()`
   expires rules past their TTL.
4. **Bounded, query-aware injection** — The plugin injects a dedicated
   `## Procedural Rules` block (default budget 5 rules / 1500 chars) containing
   only active rules whose trigger matches the turn. Rule text is sanitized
   (code fences, `SYSTEM:`/`ignore previous`, length-capped); rationale and
   evidence are never injected. On the first turn, newly-activated rules are
   flagged `[NEW]` (an injection delta tracked via a `memory_meta` hash) so
   behavioral drift is visible as it enters the prompt.
5. **CLI** — `propose-rule`, `pending-rules`, `active-rules`, `approve-rule`
   (`--ack-interactions`), `reject-rule`, `retire-rule`, `rule-conflicts`;
   `stats` now reports `pending_rules`, `active_rules`, and `open_rule_conflicts`.

Additive migration only (`CREATE TABLE IF NOT EXISTS procedural_rules` /
`rule_conflicts`); no breaking changes. Deferred from the full RFC: a separate
observation-events pipeline, rule-set version snapshots, precedence
cycle-detection, and auto-demotion of unused rules (match telemetry is captured
via `match_count` / `last_matched_at` as the foundation). Still open:
multi-agent namespaces ([#10](https://github.com/xMannixx/agent-memory-skill/issues/10)).

## References

- [Agent Long-Term Memory in 2026: Letta, Mem0, Zep, and LangMem Compared](https://agentmarketcap.ai/blog/2026/04/08/agent-long-term-memory-architecture-letta-memgpt-langmem-zep)
- [AI Memory Management for LLMs and Agents](https://mem0.ai/blog/ai-memory-management-for-llms-and-agents)
- [OWASP Agent Memory Guard](https://owasp.org/www-project-agent-memory-guard/)
- [OWASP Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [Synapse: Episodic-Semantic Memory via Spreading Activation](https://arxiv.org/abs/2601.02744)
- [HeLa-Mem: Hebbian Learning and Associative Memory for LLM Agents](https://arxiv.org/abs/2604.16839)

## Contributing

See [docs/contributing-quickstart.md](docs/contributing-quickstart.md) for the label scheme, tier definitions, and issue workflow.
