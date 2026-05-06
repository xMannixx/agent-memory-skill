"""
AgentMemory Plugin für Hermes.

Injiziert beim Start jeder Session:
- Alle identity-Facts (immer, kein Ablauf)
- Aktuelle evidence-Facts (letzte 10 zugegriffene)
- Aktuelle preference-Facts (letzte 5)
- Offene Lektionen (letzte 3 negative)

via pre_llm_call Hook — kein manuelles Laden nötig.
"""

from __future__ import annotations
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MEMORY_SRC = Path.home() / ".hermes" / "agent-memory" / "src"


def _get_memory():
    try:
        if str(_MEMORY_SRC) not in sys.path:
            sys.path.insert(0, str(_MEMORY_SRC))
        from memory import AgentMemory
        return AgentMemory()
    except Exception as e:
        logger.warning("AgentMemory konnte nicht geladen werden: %s", e)
        return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _inject_memory)


def _inject_memory(*, is_first_turn: bool = False, **_):
    """
    Wird vor jedem LLM-Call aufgerufen.
    Injiziert Memory-Kontext nur beim ersten Turn einer Session
    um Context-Window-Verbrauch zu minimieren.
    """
    if not is_first_turn:
        return None

    mem = _get_memory()
    if not mem:
        return None

    parts = []

    # identity — immer, kein Ablauf, kein Cap
    identity_facts = mem.recall_by_authority("identity", limit=20)
    if identity_facts:
        lines = [f"- {f.content}" for f in identity_facts]
        parts.append("## Identität (permanent)\n" + "\n".join(lines))

    # preference — letzte 5
    pref_facts = mem.recall_by_authority("preference", limit=5)
    if pref_facts:
        lines = [f"- {f.content}" for f in pref_facts]
        parts.append("## Präferenzen\n" + "\n".join(lines))

    # evidence — letzte 10 zugegriffene
    evidence_facts = mem.recall_by_authority("evidence", limit=10)
    if evidence_facts:
        lines = [f"- {f.content}" for f in evidence_facts]
        parts.append("## Kontext\n" + "\n".join(lines))

    # Lektionen — letzte 3 negative
    lessons = mem.get_lessons(outcome="negative", limit=3)
    if lessons:
        lines = [f"- {l.insight}" for l in lessons]
        parts.append("## Lektionen (nicht wiederholen)\n" + "\n".join(lines))

    if not parts:
        return None

    context = "# AgentMemory\n\n" + "\n\n".join(parts)
    return {"context": context}
