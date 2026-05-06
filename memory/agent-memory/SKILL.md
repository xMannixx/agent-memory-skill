---
name: agent-memory
description: "Persistent SQLite memory for Hermes: Facts, Lessons, Entities with Authority Lanes and Rebound-Protection. Auto-injects context at session start via plugin."
version: 1.1.0
author: xPerryx + Lena OpenClaw (agent-memory-1-0-0 base)
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [memory, sqlite, persistence, facts, lessons, entities, authority-lanes, plugin]
    category: memory
---

# AgentMemory Skill

Persistent memory system for Hermes Agent with structured Authority Lanes, Rebound-Protection, and automatic session injection via plugin.

Inspired by Lena OpenClaw's agent-memory-1-0-0, extended with:
- Authority Lanes (identity / preference / evidence / authorization)
- Rebound-Protection after idle phases (signalfoundry / Moltbook pattern)
- Class-specific TTL and forget_stale()
- Source-Trust hierarchy
- Auto-injection plugin (no manual loading needed)

## When to Use

Load this skill when you want persistent memory across Hermes sessions that:
- Survives restarts
- Separates identity facts from preferences and technical evidence
- Prevents memory injection attacks (authorization only from observation)
- Auto-loads context at session start without user intervention

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
python3 -m pytest tests/test_memory.py -v
# Expected: 9/9 passed
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

# Cleanup
$PYTHON $CLI forget-stale

# Record lesson
$PYTHON $CLI learn "Action" "Context" positive "Insight"
```

## Auto-Injection via Plugin

Once installed, the plugin injects memory context automatically at the first turn of every session via `pre_llm_call` hook:

- **identity**: all (no limit, never expires)
- **preference**: last 5
- **evidence**: last 10
- **negative lessons**: last 3

No manual loading required.

## Pitfalls

- On some systems `python` is not in PATH — use `python3` or full venv path.
- SQLite `:memory:` loses data when connection closes. Tests use `_shared_conn` pattern — do not change connection logic without understanding this.
- `_check_rebound()` must run after `_init_db()` — session_log table must exist first.
- `startup_skills` alone is NOT enough for auto-injection. The plugin with `pre_llm_call` hook is required.
- `authorization` facts from `conversation` source are silently rejected by design.

## Architecture Decisions

- **Floor (identity)** never decays — idle periods must not lower the entry threshold.
- **Rebound-Cap**: After >6h idle, max 3 new facts — prevents memory flooding. Identity is exempt.
- **Timer as compactor only** — writing is event-driven (on `remember()`), not time-based.
- **authorization only from observation** — prevents privilege escalation via conversation.
- **forget_stale() class-aware** — identity: never, preference: 14d, evidence: 60d, authorization: 90d.

## References

- See `references/architecture.md` for design rationale
- See `references/moltbook-discussion.md` for signalfoundry pattern origin
