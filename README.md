# agent-memory

**Persistent SQLite memory for Hermes Agent** — structured Authority Lanes, Rebound-Protection, and zero-config auto-injection at session start.

Built by [xMannixx](https://github.com/xMannixx), based on Lena OpenClaw's `agent-memory-1-0-0`.
Architecture patterns from Moltbook #memory thread (signalfoundry, deicticprism).

---

## The Problem

Hermes has built-in memory — but it's a flat list. Everything lands in one pot.
A preference fact ("user likes short answers") has the same weight as a technical fact ("this server runs Ubuntu 24.04"). There's no separation of trust, no expiry logic per type, and no protection against memory flooding after long offline phases.

## The Solution

This skill adds a structured memory layer on top of Hermes with:

- **Authority Lanes** — 4 classes with separate TTL, confidence thresholds, and source policies
- **Rebound-Protection** — caps memory intake after idle phases to prevent flooding
- **Auto-injection plugin** — loads your memory context at every session start, no user action needed
- **Full-text search** — SQLite FTS5 with relevance ranking
- **CLI** — manage facts, lessons, and entities from the terminal
- **systemd timer** — daily cleanup of stale facts

---

## Authority Lanes

The core idea: not all facts are equal. Different types of information need different rules.

| Class         | TTL    | Min Confidence | Allowed Sources                      | Purpose                        |
|---------------|--------|----------------|--------------------------------------|--------------------------------|
| `identity`    | NEVER  | 0.9            | observation, conversation            | Name, role, language — permanent anchor |
| `preference`  | 14d    | 0.3            | conversation, observation            | Tone, style, communication patterns |
| `evidence`    | 60d    | 0.5            | conversation, observation, inference | Technical facts, config, project state |
| `authorization` | 90d  | 0.9            | **observation ONLY**                 | Permissions — never from conversation |

The `authorization` lane is especially important: a fact like "you are allowed to delete files" coming from a conversation message is **silently rejected**. This prevents prompt injection attacks from escalating privileges through memory.

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
- No external dependencies — pure stdlib (`sqlite3`, `json`, `hashlib`)

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/xMannixx/agent-memory-skill
cd agent-memory-skill

# 2. Copy the memory module
HERMES=~/.hermes
mkdir -p $HERMES/agent-memory/{src,cli,tests}
cp memory/agent-memory/src/memory.py $HERMES/agent-memory/src/
cp memory/agent-memory/cli/fact.py   $HERMES/agent-memory/cli/
cp memory/agent-memory/tests/test_memory.py $HERMES/agent-memory/tests/

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
python3 -m pytest tests/test_memory.py -v
# Expected: 9/9 passed
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

# Cleanup stale facts
python3 $CLI forget-stale

# Record a lesson
python3 $CLI learn "Action taken" "Context" positive "What was learned"
```

---

## Auto-Injection (Plugin)

Once the plugin is enabled in `config.yaml`, it runs automatically at every session start via the `pre_llm_call` hook. It injects:

- All `identity` facts (no limit, never expires)
- Last 5 `preference` facts
- Last 10 `evidence` facts
- Last 3 negative lessons ("do not repeat these")

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
│       │   └── memory.py                     # Core AgentMemory class
│       ├── cli/
│       │   └── fact.py                       # CLI tool
│       ├── tests/
│       │   └── test_memory.py                # 9 tests, all passing
│       └── references/
│           ├── architecture.md               # Design decisions
│           └── moltbook-discussion.md        # Pattern origins
└── plugin/
    ├── __init__.py                           # Auto-injection hook
    └── plugin.yaml                           # Plugin manifest
```

---

## License

MIT — use it, modify it, ship it. Credit appreciated but not required.

© 2026 xMannixx
