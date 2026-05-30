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
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_MEMORY_SRC = Path.home() / ".hermes" / "agent-memory" / "src"
if str(_MEMORY_SRC) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SRC))

try:
    from memory import AgentMemory
except Exception:
    AgentMemory = None

DEFAULT_BUDGETS = {
    "identity": {"limit": 20, "max_chars": 4000},
    "preference": {"limit": 5, "max_chars": 1600},
    "evidence": {"limit": 10, "max_chars": 3000},
    "lessons": {"limit": 3, "max_chars": 1200},
}


def _get_memory():
    if AgentMemory is None:
        logger.warning("AgentMemory konnte nicht geladen werden")
        return None
    try:
        return AgentMemory()
    except Exception as e:
        logger.warning("AgentMemory konnte nicht geladen werden: %s", e)
        return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _inject_memory)


def _budget_for(lane: str, budgets: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    budget = dict(budgets[lane])
    env_limit = os.getenv(f"AGENT_MEMORY_BUDGET_{lane.upper()}")
    if env_limit:
        try:
            budget["limit"] = max(0, int(env_limit))
        except ValueError:
            logger.warning("Ignoriere ungueltiges Budget fuer %s: %s", lane, env_limit)
    return budget


def _clip_to_budget(lines: Iterable[str], max_chars: int) -> List[str]:
    clipped = []
    used = 0
    for line in lines:
        next_used = used + len(line) + (1 if clipped else 0)
        if next_used > max_chars:
            break
        clipped.append(line)
        used = next_used
    return clipped


def _section(title: str, lines: Iterable[str], max_chars: int) -> Optional[str]:
    clipped = _clip_to_budget(lines, max_chars)
    if not clipped:
        return None
    return title + "\n" + "\n".join(clipped)


def build_memory_context(
    mem: Any,
    *,
    is_first_turn: bool,
    user_message: Optional[str] = None,
    budgets: Dict[str, Dict[str, int]] = DEFAULT_BUDGETS,
) -> Optional[str]:
    """Build bounded prompt context. Authorization facts are never injected."""
    if not is_first_turn:
        return None

    parts = []

    # identity — immer, kein Ablauf; aber trotzdem budget-begrenzt.
    identity_budget = _budget_for("identity", budgets)
    identity_facts = mem.recall_by_authority("identity", limit=identity_budget["limit"])
    if identity_facts:
        lines = [f"- {f.content}" for f in identity_facts]
        section = _section(
            "## Identität (permanent)",
            lines,
            identity_budget["max_chars"],
        )
        if section:
            parts.append(section)

    # preference — letzte 5
    pref_budget = _budget_for("preference", budgets)
    pref_facts = mem.recall_by_authority("preference", limit=pref_budget["limit"])
    if pref_facts:
        lines = [f"- {f.content}" for f in pref_facts]
        section = _section("## Präferenzen", lines, pref_budget["max_chars"])
        if section:
            parts.append(section)

    # evidence — letzte 10 zugegriffene
    evidence_budget = _budget_for("evidence", budgets)
    evidence_facts = mem.recall_by_authority("evidence", limit=evidence_budget["limit"])
    if evidence_facts:
        lines = [f"- {f.content}" for f in evidence_facts]
        section = _section("## Kontext", lines, evidence_budget["max_chars"])
        if section:
            parts.append(section)

    # Lektionen — letzte 3 negative
    lessons_budget = _budget_for("lessons", budgets)
    lessons = mem.get_lessons(outcome="negative", limit=lessons_budget["limit"])
    if lessons:
        lines = [f"- {l.insight}" for l in lessons]
        section = _section(
            "## Lektionen (nicht wiederholen)",
            lines,
            lessons_budget["max_chars"],
        )
        if section:
            parts.append(section)

    if not parts:
        return None

    return "# AgentMemory\n\n" + "\n\n".join(parts)


def _inject_memory(*, is_first_turn: bool = False, **_):
    """
    Wird vor jedem LLM-Call aufgerufen.
    Injiziert Memory-Kontext nur beim ersten Turn einer Session
    um Context-Window-Verbrauch zu minimieren.
    """
    mem = _get_memory()
    if not mem:
        return None

    context = build_memory_context(mem, is_first_turn=is_first_turn)
    if not context:
        return None
    return {"context": context}
