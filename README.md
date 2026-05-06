# agent-memory

**Persistent SQLite memory for Hermes Agent** with Authority Lanes, Rebound-Protection, and automatic session injection.

Built by [xPerryx](https://github.com/xPerryx) based on Lena OpenClaw's `agent-memory-1-0-0`.
Patterns from Moltbook #memory thread (signalfoundry, deicticprism).

---

## Features

- **Authority Lanes** — 4 classes with separate TTL, confidence, and source policies
- **Rebound-Protection** — caps memory intake after idle phases
- **Auto-injection plugin** — injects context at session start, zero user action needed
- **CLI** — manage facts from terminal
- **FTS5 search** — full-text search with relevance ranking
- **systemd timer** — daily cleanup of stale facts

## Authority Lanes

| Class         | TTL   | Min Confidence | Source          | Notes                   |
|---------------|-------|----------------|-----------------|-------------------------|
| identity      | NEVER | 0.9            | any             | Floor — never expires   |
| preference    | 14d   | 0.3            | conversation    | Tone, style             |
| evidence      | 60d   | 0.5            | any             | Technical facts         |
| authorization | 90d   | 0.9            | observation only| Never from conversation |

## Install

```bash
# 1. Clone
git clone https://github.com/xPerryx/agent-memory-skill
cd agent-memory-skill

# 2. Copy files
HERMES=~/.hermes
mkdir -p $HERMES/agent-memory/{src,cli,tests}
cp memory/agent-memory/src/memory.py $HERMES/agent-memory/src/
cp memory/agent-memory/cli/fact.py   $HERMES/agent-memory/cli/
cp memory/agent-memory/tests/test_memory.py $HERMES/agent-memory/tests/

# 3. Install plugin
mkdir -p $HERMES/plugins/agent-memory-plugin
cp plugin/__init__.py plugin/plugin.yaml $HERMES/plugins/agent-memory-plugin/

# 4. Enable in config.yaml
# Add under plugins.enabled:
#   - agent-memory-plugin

# 5. Test
cd ~/.hermes/agent-memory && python3 -m pytest tests/ -v
```

## Or via Hermes Skills Hub

```bash
hermes skills tap add https://github.com/xPerryx/agent-memory-skill
hermes skills install memory/agent-memory
```

## Quick usage

```python
from memory import AgentMemory
mem = AgentMemory()
mem.remember("User name is Manni", authority_class="identity", source="observation", confidence=1.0)
facts = mem.recall("name")
```

```bash
python3 ~/.hermes/agent-memory/cli/fact.py add "Server IP 10.0.0.1" --authority evidence --source observation
python3 ~/.hermes/agent-memory/cli/fact.py stats
```

## License

MIT
