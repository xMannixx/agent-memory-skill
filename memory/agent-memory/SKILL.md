---
name: agent-memory
description: "Persistent SQLite memory for Hermes: Facts, snippets, lessons, entities, Authority Lanes, Rebound-Protection, and budgeted German-aware query retrieval."
version: 3.0.0
author: xPerryx + Lena OpenClaw (agent-memory-1-0-0 base)
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [memory, sqlite, persistence, facts, snippets, lessons, entities, authority-lanes, plugin, retrieval]
    category: memory
---

# AgentMemory Skill

Persistent memory system for Hermes Agent with structured Authority Lanes, raw recall snippets, Rebound-Protection, and budgeted plugin retrieval.

Inspired by Lena OpenClaw's agent-memory-1-0-0, extended with:
- Authority Lanes (identity / preference / evidence / authorization)
- Rebound-Protection after idle phases (signalfoundry / Moltbook pattern)
- Class-specific TTL and forget_stale()
- Source-Trust hierarchy
- Raw recall snippets kept separate from semantic facts
- Auto-injection plugin with per-lane budgets and German-aware, score-ranked query retrieval (token-prefix FTS + synonyms, deterministic, no embeddings)

## When to Use

Load this skill when you want persistent memory across Hermes sessions that:
- Survives restarts
- Separates identity facts from preferences and technical evidence
- Keeps raw conversation snippets searchable without auto-injecting them
- Prevents memory injection attacks (authorization only from observation)
- Auto-loads bounded context at session start and retrieves relevant evidence later

## Installation

### 1. Copy files

```bash
HERMES=~/.hermes

# Core memory module
mkdir -p $HERMES/agent-memory/src
mkdir -p $HERMES/agent-memory/cli
mkdir -p $HERMES/agent-memory/tests

cp src/memory.py $HERMES/agent-memory/src/
cp cli/fact.py   $HERMES/agent-memory/cli/
cp tests/test_memory.py $HERMES/agent-memory/tests/
cp tests/test_plugin.py $HERMES/agent-memory/tests/
```

### 2. Install plugin

```bash
mkdir -p $HERMES/plugins/agent-memory-plugin
cp plugin/__init__.py  $HERMES/plugins/agent-memory-plugin/
cp plugin/plugin.yaml  $HERMES/plugins/agent-memory-plugin/
```

### 3. Enable plugin in config.yaml

```yaml
plugins:
  enabled:
  - agent-memory-plugin
```

### 4. (Optional) systemd cleanup timer

```bash
# Runs forget_stale() daily
systemctl --user enable --now hermes-memory-cleanup.timer
```

### 5. Verify

```bash
cd ~/.hermes/agent-memory
python3 -m pytest tests -v
# Expected: 106 passed
```

## Authority Lanes

| Class         | TTL   | Min Confidence | Allowed Sources                       | Notes                     |
|---------------|-------|----------------|---------------------------------------|---------------------------|
| identity      | NEVER | 0.9            | observation, conversation             | Floor — never expires     |
| preference    | 14d   | 0.3            | conversation, observation             | Tone, style, language     |
| evidence      | 60d   | 0.5            | conversation, observation, inference  | Technical facts, claims   |
| authorization | 90d   | 0.9            | observation ONLY                      | Never from conversation   |

## Rebound-Protection

After >6h idle: max 3 new facts accepted per session (except identity).
Prevents memory flooding after long offline phases.

## Sliding TTL

Non-identity facts expire by last access, not only by creation time. `recall()`,
`recall_by_authority()`, and `get_fact()` all refresh `last_accessed` and extend
`expires_at` according to the fact's authority lane. This keeps facts alive when
the auto-injection plugin actively uses them.

## Python Usage

```python
import sys
sys.path.insert(0, str(Path.home() / '.hermes/agent-memory/src'))
from memory import AgentMemory

mem = AgentMemory()

# Store a fact
mem.remember("User's name is Manni", authority_class="identity",
             source="observation", confidence=1.0)

# Store preference
mem.remember("Prefers German language", authority_class="preference",
             source="observation", confidence=0.9)

# Search
facts = mem.recall("name")

# All identity facts
identity = mem.recall_by_authority("identity")

# Record a lesson
mem.learn(action="Deployed without fallback",
          context="hermes-setup", outcome="negative",
          insight="Always configure a fallback provider")

# Track an entity
mem.track_entity("Manni", "person", {"username": "xPerryx", "language": "de"})

# Store raw recall separately from facts
mem.remember_snippet("Discussed query-aware retrieval", session_id="demo")
snippets = mem.search_snippets("query-aware", session_id="demo")

# Stats
print(mem.stats())

# Daily cleanup (also runs via systemd timer)
mem.forget_stale()
```

## CLI Usage

```bash
PYTHON=python3
CLI=~/.hermes/agent-memory/cli/fact.py

# Add fact
$PYTHON $CLI add "User name is Manni" --authority identity --source observation --confidence 1.0

# Search
$PYTHON $CLI recall "name"

# List by class
$PYTHON $CLI list --authority identity

# Stats
$PYTHON $CLI stats

# Raw recall snippets
$PYTHON $CLI snippet add "Discussed local-first retrieval" --session demo
$PYTHON $CLI snippet search retrieval --session demo

# Audit / snapshots / consolidation
$PYTHON $CLI audit --limit 10
$PYTHON $CLI snapshot --label before-change
$PYTHON $CLI consolidate --dry-run

# Cleanup
$PYTHON $CLI forget-stale

# Record lesson
$PYTHON $CLI learn "Action" "Context" positive "Insight"
```

## Auto-Injection via Plugin

Once installed, the plugin builds memory context automatically via the `pre_llm_call` hook.

First turn:

- **identity**: permanent floor, budget-limited
- **preference**: last 5
- **evidence**: last 10
- **negative lessons**: last 3

Later turns:

- no injection unless the hook provides a current user message
- identity remains available as a small floor
- relevant evidence is retrieved from the user message
- all lanes are clipped by per-lane budgets

`authorization` facts are never prompt-injected. They are allowed only from
`observation` source and should be used by explicit code paths, not automatic
context injection.

No manual loading required.

## Pitfalls

- On some systems `python` is not in PATH — use `python3` or full venv path.
- SQLite `:memory:` loses data when connection closes. Tests use `_shared_conn` pattern — do not change connection logic without understanding this.
- `_check_rebound()` must run after `_init_db()` — `memory_meta` must exist first.
- `startup_skills` alone is NOT enough for auto-injection. The plugin with `pre_llm_call` hook is required.
- `authorization` facts from `conversation` source are silently rejected by design.
- Raw snippets are recall memory, not facts. Store them with `remember_snippet()` and search them with `search_snippets()`.
- The plugin uses character budgets instead of a tokenizer to avoid extra runtime dependencies.

## Architecture Decisions

- **Floor (identity)** never decays — idle periods must not lower the entry threshold.
- **Rebound-Cap**: After >6h idle, max 3 new facts — prevents memory flooding. Identity is exempt.
- **Sliding TTL**: Read access refreshes non-identity expiry, so active facts survive cleanup.
- **Recall snippets are separate**: raw conversation memory does not pollute semantic facts and is not auto-injected.
- **Prompt budgets**: plugin context is clipped per lane to keep first-turn and later-turn prompts bounded.
- **Timer as compactor only** — writing is event-driven (on `remember()`), not time-based.
- **authorization only from observation** — prevents privilege escalation via conversation.
- **authorization never auto-injected** — sensitive permission memory is not placed into prompts by default.
- **forget_stale() class-aware** — identity: never, preference: 14d, evidence: 60d, authorization: 90d.

## References

- See `references/architecture.md` for design rationale
- See `references/moltbook-discussion.md` for signalfoundry pattern origin
