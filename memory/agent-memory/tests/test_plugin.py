"""Tests fuer das Hermes AgentMemory Plugin."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
MEMORY_SRC = ROOT / "memory" / "agent-memory" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(MEMORY_SRC) not in sys.path:
    sys.path.insert(0, str(MEMORY_SRC))

from memory import AgentMemory
from plugin import build_memory_context


@pytest.fixture
def mem():
    return AgentMemory(db_path=":memory:")


def minimal_budgets(**overrides):
    budgets = {
        "identity": {"limit": 20, "max_chars": 4000},
        "preference": {"limit": 5, "max_chars": 1600},
        "evidence": {"limit": 10, "max_chars": 3000},
        "lessons": {"limit": 3, "max_chars": 1200},
    }
    for lane, values in overrides.items():
        budgets[lane].update(values)
    return budgets


def test_plugin_first_turn_baseline_includes_existing_lanes(mem):
    mem.remember(
        "Perry is the operator",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )
    mem.remember(
        "Prefer compact answers",
        authority_class="preference",
        source="conversation",
        confidence=0.9,
    )
    mem.remember(
        "Hermes is testing retrieval",
        authority_class="evidence",
        source="conversation",
        confidence=0.8,
    )
    mem.learn(
        "ran tests",
        "plugin",
        "negative",
        "Do not inject unlimited memory",
    )

    context = build_memory_context(mem, is_first_turn=True)

    assert context is not None
    assert "## Identität (permanent)" in context
    assert "Perry is the operator" in context
    assert "## Präferenzen" in context
    assert "Prefer compact answers" in context
    assert "## Kontext" in context
    assert "Hermes is testing retrieval" in context
    assert "## Lektionen (nicht wiederholen)" in context
    assert "Do not inject unlimited memory" in context


def test_plugin_budget_clips_on_line_boundaries(mem):
    mem.remember(
        "Alpha",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )
    mem.remember(
        "Beta",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )

    context = build_memory_context(
        mem,
        is_first_turn=True,
        budgets=minimal_budgets(
            identity={"limit": 10, "max_chars": 8},
            preference={"limit": 0},
            evidence={"limit": 0},
            lessons={"limit": 0},
        ),
    )

    assert context is not None
    assert context.count("- ") == 1


def test_plugin_never_injects_authorization_lane(mem):
    mem.remember(
        "Operator authorized production deploys",
        authority_class="authorization",
        source="observation",
        confidence=1.0,
    )

    context = build_memory_context(mem, is_first_turn=True)

    assert context is None or "production deploys" not in context


def test_plugin_returns_none_after_first_turn_for_budget_batch(mem):
    mem.remember(
        "Perry is the operator",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )

    assert build_memory_context(mem, is_first_turn=False) is None
