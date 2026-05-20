# Agent Memory Roadmap

`agent-memory-skill` is a small, local-first memory layer for Hermes Agent. The next phase is about turning the current prototype into a durable memory subsystem: safer storage, better retrieval, clearer lifecycle rules, and security controls against memory poisoning.

The roadmap is organized by release milestones. Each item is tracked as a GitHub issue with labels for priority tier, area, and change type.

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| [v1.2 - Stability & Hygiene](https://github.com/xMannixx/agent-memory-skill/milestones) | Make the current SQLite memory core predictable, indexed, testable, and easier to maintain. | Planned |
| [v1.3 - Audit & Recovery](https://github.com/xMannixx/agent-memory-skill/milestones) | Add auditability, rollback, anomaly detection, and operational stats. | Planned |
| [v1.4 - Consolidation & Decay](https://github.com/xMannixx/agent-memory-skill/milestones) | Move from simple retention windows toward consolidation and confidence decay. | Planned |
| [v2.0 - Smart Retrieval](https://github.com/xMannixx/agent-memory-skill/milestones) | Add richer retrieval paths: recall memory, hybrid search, smarter plugin injection. | Planned |

## Priority Tiers

| Tier | Meaning |
|------|---------|
| `tier:must-do` | Fixes or hardening work that protects correctness, data integrity, or security. |
| `tier:high-value` | Conceptually important improvements with a strong practical payoff. |
| `tier:exploratory` | Larger retrieval or architecture upgrades that need careful design before implementation. |
| `tier:polish` | Maintainability, scale, testing, and usability improvements. |

## Planned Work

| # | Issue | Milestone | Tier | Area |
|---|-------|-----------|------|------|
| 1 | `feat(core): introduce recall lane for raw conversation snippets` | v2.0 | `tier:exploratory` | `area:core`, `area:schema` |
| 2 | `feat(cli): consolidate command to merge related facts` | v1.4 | `tier:high-value` | `area:core`, `area:cli` |
| 3 | `feat(search): hybrid FTS5 + sqlite-vec retrieval with RRF` | v2.0 | `tier:exploratory` | `area:search` |
| 4 | `feat(security): audit log, snapshots and rapid-change anomaly detection` | v1.3 | `tier:must-do` | `area:security`, `area:schema` |
| 5 | `feat(plugin): query-aware retrieval at second turn onward` | v2.0 | `tier:exploratory` | `area:plugin` |
| 6 | `feat(core): exponential confidence decay per authority lane` | v1.4 | `tier:high-value` | `area:core`, `area:schema` |
| 7 | `fix(core): make remember idempotent via content-hash IDs` | v1.2 | `tier:must-do` | `area:core` |
| 8 | `feat(schema): namespaces for multi-user and multi-agent memory` | Backlog | `tier:polish` | `area:schema` |
| 9 | `feat(core): self-observability stats and latency counters` | v1.3 | `tier:polish` | `area:core`, `area:cli` |
| 10 | `perf(schema): add facts and lessons indexes and enable WAL` | v1.2 | `tier:must-do` | `area:schema` |
| 11 | `feat(core): lesson and entity lifecycle with decay on use` | v1.2 | `tier:polish` | `area:core`, `area:schema` |
| 12 | `feat(core): finer source trust graduation and promotion rules` | Backlog | `tier:polish` | `area:core` |
| 13 | `feat(plugin): token budget, lazy tool retrieval, authorization lane decision` | v2.0 | `tier:exploratory` | `area:plugin` |
| 14 | `test(infra): file-DB coverage, property tests, frozen-time helpers` | v1.2 | `tier:polish` | `area:tests` |
| 15 | `refactor(core): small hygiene umbrella` | v1.2 | `tier:polish` | `area:core` |

## Release Themes

### v1.2 - Stability & Hygiene

This release keeps behavior close to the current implementation while making it safer to grow. The focus is idempotent writes, schema indexes, WAL mode, lifecycle coverage for lessons/entities, file-backed tests, and small hygiene fixes.

### v1.3 - Audit & Recovery

Persistent memory needs a forensic trail. This milestone adds audit logging, snapshots, rollback, rapid-change anomaly detection, and basic observability counters. The security direction is informed by OWASP Agent Memory Guard and prompt-injection prevention guidance.

### v1.4 - Consolidation & Decay

This milestone moves beyond hard TTLs. The goal is to consolidate repeated evidence into stronger facts and introduce authority-lane-specific confidence decay. It is inspired by current work on reflective, episodic, and semantic memory systems.

### v2.0 - Smart Retrieval

This is the architectural retrieval milestone. It explores a recall lane for raw conversation snippets, hybrid FTS5 plus vector search, and smarter plugin injection that retrieves relevant memory instead of blindly injecting fixed top-N facts.

## References

- [Agent Long-Term Memory in 2026: Letta, Mem0, Zep, and LangMem Compared](https://agentmarketcap.ai/blog/2026/04/08/agent-long-term-memory-architecture-letta-memgpt-langmem-zep)
- [AI Memory Management for LLMs and Agents](https://mem0.ai/blog/ai-memory-management-for-llms-and-agents)
- [OWASP Agent Memory Guard](https://owasp.org/www-project-agent-memory-guard/)
- [OWASP Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [Hybrid full-text search and vector search with SQLite](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [Synapse: Episodic-Semantic Memory via Spreading Activation](https://arxiv.org/abs/2601.02744)
- [HeLa-Mem: Hebbian Learning and Associative Memory for LLM Agents](https://arxiv.org/abs/2604.16839)

## Contributing

See [docs/contributing-quickstart.md](docs/contributing-quickstart.md) for the label scheme, tier definitions, and issue workflow.
