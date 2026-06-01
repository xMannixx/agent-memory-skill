<p align="center">
  <img src="assets/logo.png" alt="AgentMemory logo" width="200">
</p>

<h1 align="center">agent-memory</h1>

<p align="center"><strong>Persistent SQLite memory for Hermes Agent</strong><br>structured Authority Lanes, recall snippets, Rebound-Protection, and budgeted memory injection.</p>

<p align="center">
  <a href="https://github.com/xMannixx/agent-memory-skill/actions/workflows/ci.yml"><img src="https://github.com/xMannixx/agent-memory-skill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.8%2B-blue.svg" alt="Python 3.8+"></a>
  <a href="requirements-dev.txt"><img src="https://img.shields.io/badge/deps-stdlib%20only-success.svg" alt="Dependencies: stdlib only"></a>
</p>

Built by [xMannixx](https://github.com/xMannixx), based on Lena OpenClaw's `agent-memory-1-0-0`.
Architecture patterns from Moltbook #memory thread (signalfoundry, deicticprism).

---

## The Problem

Hermes has built-in memory — but it's a flat list. Everything lands in one pot.
A preference fact ("user likes short answers") has the same weight as a technical fact ("this server runs Ubuntu 24.04"). There's no separation of trust, no expiry logic per type, and no protection against memory flooding after long offline phases.

## The Solution

This skill adds a structured memory layer on top of Hermes with:

- **Authority Lanes** — 4 classes with separate TTL, confidence thresholds, and source policies
- **Recall snippets** — raw conversation recall stored separately from distilled semantic facts
- **Rebound-Protection** — caps memory intake after idle phases to prevent flooding
- **Smart plugin injection** — first-turn baseline plus query-aware evidence retrieval on later turns
- **Token budgeting** — per-lane context limits with explicit no-injection policy for authorization facts
- **German-aware retrieval** — token-prefix FTS5 + synonym map, with fold/stem relevance scoring (deterministic, no embeddings)
- **Finer source trust** — five source categories with per-lane write policy; `tool` and `external` input quarantined to `evidence` only (cannot write `identity` or `authorization`)
- **Conflict detection** — non-blocking detection of contradictory facts in single-valued lanes (`identity`, `authorization`) with explicit resolution; open conflicts auto-reconcile when a referenced fact becomes inactive
- **Entity relations** — lightweight directed graph between entities (no embeddings), with lifecycle cleanup
- **Relation-aware recall** — on query turns, bounded 1-hop relation expansion into prompt context (edge-only, no fact/authorization leak)
- **Audit, snapshots, and stats** — recovery trail, rollback, anomaly detection, open-conflict and relation counts, and recall latency counters
- **Provenance** — read-only reconstruction of a fact's audit chain (write, update, supersede, forget, conflict) from the append-only audit log; no duplicate provenance storage
- **CLI** — manage facts, snippets, lessons, entities, relations, conflicts, snapshots, provenance, and consolidation from the terminal
- **systemd timer** — daily cleanup of stale facts

---

## Authority Lanes

The core idea: not all facts are equal. Different types of information need different rules.

| Class         | TTL    | Min Confidence | Allowed Sources                                      | Purpose                        |
|---------------|--------|----------------|------------------------------------------------------|--------------------------------|
| `identity`    | NEVER  | 0.9            | observation, conversation                            | Name, role, language — permanent anchor |
| `preference`  | 14d    | 0.3            | observation, conversation                            | Tone, style, communication patterns |
| `evidence`    | 60d    | 0.5            | observation, conversation, inference, tool, external | Technical facts, config, project state (quarantine lane for lower-trust input) |
| `authorization` | 90d  | 0.9            | **observation ONLY**                                 | Permissions — never from conversation or external/tool sources |

Trust order (most to least): `observation` > `conversation` > `inference` > `tool` > `external`. The `authorization` lane accepts only `observation`. `tool` and `external` sources can write **only** `evidence` — never `identity` or `authorization` — so poisoned external or tool output cannot escalate into identity or permission memory.

---

## Rebound-Protection

**The problem:** After being offline for hours, the memory baseline drifts down. On resume, a flood of incoming facts tries to re-anchor — and without a cap, the agent accepts all of them, polluting memory with stale or injected data.

**The fix:** A session-level batch counter.
- First write after >6h idle activates rebound mode
- Max 3 non-identity facts accepted per session in rebound mode
- `identity` is always exempt — the floor must never be gated
- Next session starts fresh

Credit: signalfoundry on Moltbook #memory

---

## Installation

### Requirements

- Hermes Agent (any recent version)
- Python 3.8+
- No external runtime dependencies — pure stdlib (`sqlite3`, `json`, `hashlib`, `re`)
- Development only: `pytest` (see [requirements-dev.txt](requirements-dev.txt))

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/xMannixx/agent-memory-skill
cd agent-memory-skill

# 2. Copy the memory module
HERMES=~/.hermes
mkdir -p $HERMES/agent-memory/{src,cli,tests/fixtures}
cp memory/agent-memory/src/memory.py    $HERMES/agent-memory/src/
cp memory/agent-memory/src/text_norm.py $HERMES/agent-memory/src/
cp memory/agent-memory/src/synonyms.json $HERMES/agent-memory/src/
cp memory/agent-memory/cli/fact.py      $HERMES/agent-memory/cli/
cp memory/agent-memory/tests/*.py        $HERMES/agent-memory/tests/
cp memory/agent-memory/tests/fixtures/retrieval_eval.json $HERMES/agent-memory/tests/fixtures/

# 3. Install the auto-injection plugin
mkdir -p $HERMES/plugins/agent-memory-plugin
cp plugin/__init__.py plugin/plugin.yaml $HERMES/plugins/agent-memory-plugin/

# 4. Enable the plugin in ~/.hermes/config.yaml
# Add this under the plugins section:
#
# plugins:
#   enabled:
#   - agent-memory-plugin

# 5. Run tests to verify
cd ~/.hermes/agent-memory
python3 -m pytest tests -v
# Expected: 148 passed
```

### Via Hermes Skills Hub

```bash
hermes skills tap add https://github.com/xMannixx/agent-memory-skill
hermes skills install memory/agent-memory
```

---

## Usage

### Python API

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / '.hermes/agent-memory/src'))
from memory import AgentMemory

mem = AgentMemory()

# Store identity fact (permanent, never expires)
mem.remember("User's name is Manni", authority_class="identity",
             source="observation", confidence=1.0)

# Store preference (expires after 14d without access)
mem.remember("Prefers German language", authority_class="preference",
             source="conversation", confidence=0.9)

# Store technical evidence
mem.remember("VPS runs Ubuntu 24.04", authority_class="evidence",
             source="observation", confidence=1.0)

# Search (FTS5 full-text)
facts = mem.recall("name")

# Get all facts of a class
identity_facts = mem.recall_by_authority("identity")

# Record a lesson from a mistake
mem.learn(
    action="Deployed without fallback provider",
    context="hermes-setup",
    outcome="negative",
    insight="Always configure a fallback provider before going live"
)

# Track an entity
mem.track_entity("Manni", "person", {"username": "xMannixx", "language": "de"})

# Entity relations (lightweight graph)
mem.relate("Manni", "arbeitet_bei", "arriva", from_type="person", to_type="org")
mem.get_relations("Manni", direction="out")
neighbors = mem.related_entities("Manni", predicate="arbeitet_bei")

# Conflict detection (single-valued lanes; tag the subject)
mem.remember("User role is admin", authority_class="identity",
             source="observation", confidence=1.0, tags=["user", "role"])
open_conflicts = mem.get_conflicts()
# mem.resolve_conflict(keep_id, [drop_id, ...])

# Store raw conversation recall separately from facts
mem.remember_snippet(
    "Raw conversation detail before it is distilled into a fact",
    session_id="session-2026-05-30",
    metadata={"turn": 12},
)
snippets = mem.search_snippets("conversation detail", session_id="session-2026-05-30")

# Stats
print(mem.stats())
# {'active_facts': 9, 'by_class': {'identity': 5, 'preference': 2, 'evidence': 2}, ...}

# Daily cleanup (stale facts per TTL policy)
mem.forget_stale()
```

### CLI

```bash
CLI=~/.hermes/agent-memory/cli/fact.py

# Add a fact
python3 $CLI add "Server IP is 10.0.0.1" --authority evidence --source observation --confidence 1.0

# Search
python3 $CLI recall "server"

# List all facts of a class
python3 $CLI list --authority identity

# Stats
python3 $CLI stats

# Raw recall snippets
python3 $CLI snippet add "Discussed local-first retrieval design" --session demo
python3 $CLI snippet search retrieval --session demo

# Entity relations
python3 $CLI relate Manni arbeitet_bei arriva --from-type person --to-type org
python3 $CLI relations Manni --direction out --predicate arbeitet_bei

# Conflicts (single-valued lanes)
python3 $CLI conflicts
python3 $CLI conflicts --all
python3 $CLI resolve-conflict <keep_id> <drop_id> [drop_id ...]

# Audit, snapshots, provenance, and consolidation
python3 $CLI audit --limit 10
python3 $CLI snapshot --label before-refactor
python3 $CLI snapshots
python3 $CLI provenance <fact_id>
python3 $CLI consolidate --dry-run

# Cleanup stale facts
python3 $CLI forget-stale

# Record a lesson
python3 $CLI learn "Action taken" "Context" positive "What was learned"
```

---

## Auto-Injection (Plugin)

Once the plugin is enabled in `config.yaml`, it runs automatically via the `pre_llm_call` hook.

On the first turn of a session it injects a compact baseline:

- `identity` facts (permanent floor, budget-limited)
- Last 5 `preference` facts
- Last 10 `evidence` facts
- Last 3 negative lessons ("do not repeat these")

On later turns it stays quiet unless the hook receives a current user message. If a message is available, it keeps the identity floor and retrieves query-relevant `evidence` facts via German-aware recall (token-prefix FTS + synonyms), then ranks candidates by a relevance score (stem/synonym overlap) without a binary cutoff. The only hard limit is the per-lane character budget.

When a user message mentions known entities (by normalized term overlap with entity names), the plugin also injects their direct (1-hop) relations under a `## Related` section — relation edges only, never facts, so authorization content cannot leak through this path. Expansion is bounded (default: 6 lines / 1000 characters, at most 3 matched entities per turn). Disable with `AGENT_MEMORY_RELATIONS=0` (or `false` / `no` / `off`). Override the relations lane budget with `AGENT_MEMORY_BUDGET_RELATIONS`. Optionally append neighbor entity attributes to each relation line via `AGENT_MEMORY_BUDGET_ENTITY_ATTRS` (integer, default `0` = disabled): when set to N > 0, up to N `key=value` pairs per neighbor entity (sorted by key) are shown in brackets on that line, still clipped by the relations character budget.

Open conflicts in single-valued lanes are automatically reconciled when either referenced fact is superseded or removed (`consolidate()`, `supersede()`, `forget()`, `forget_stale()`), so `stats()["open_conflicts"]` and `get_conflicts()` do not list stale pairs.

`authorization` facts are never prompt-injected. They can be stored only from `observation` source and remain available for explicit code paths, not automatic prompt context.

No `/skill` command needed. No manual loading. It just works.

---

## Optional: systemd Cleanup Timer

Runs `forget_stale()` daily to prune expired facts according to TTL policy.

```bash
# Check status
systemctl --user status hermes-memory-cleanup.timer
```

---

## File Structure

```
agent-memory-skill/
├── README.md
├── memory/
│   └── agent-memory/
│       ├── SKILL.md                          # Hermes skill definition
│       ├── src/
│       │   ├── memory.py                     # Core AgentMemory class
│       │   ├── text_norm.py                  # German fold/stem/synonym helpers
│       │   └── synonyms.json                 # Editable synonym map for recall
│       ├── cli/
│       │   └── fact.py                       # CLI tool
│       ├── tests/
│       │   ├── test_memory.py                # Core memory tests
│       │   ├── test_text_norm.py             # Normalization tests
│       │   ├── test_plugin.py                # Plugin retrieval/budget tests
│       │   ├── test_retrieval_eval.py        # Retrieval eval harness
│       │   └── fixtures/
│       │       └── retrieval_eval.json       # Eval set (positives/negatives/regressions)
│       └── references/
│           ├── architecture.md               # Design decisions
│           └── moltbook-discussion.md        # Pattern origins
└── plugin/
    ├── __init__.py                           # Auto-injection hook
    └── plugin.yaml                           # Plugin manifest
```

---

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — release history (v1.1 through v3.5)
- [ROADMAP.md](ROADMAP.md) — milestones and planned work
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, commit and PR conventions
- [SECURITY.md](SECURITY.md) — how to report vulnerabilities and the memory threat model
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — community expectations
- [memory/agent-memory/references/architecture.md](memory/agent-memory/references/architecture.md) — design rationale

---

## License

MIT — use it, modify it, ship it. Credit appreciated but not required.

© 2026 xMannixx
