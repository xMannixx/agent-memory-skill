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
from plugin import _extract_user_message, build_memory_context, memory_status


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
    assert "## Identity (permanent)" in context
    assert "Perry is the operator" in context
    assert "## Preferences" in context
    assert "Prefer compact answers" in context
    assert "## Context" in context
    assert "Hermes is testing retrieval" in context
    assert "## Lessons (do not repeat)" in context
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


def test_plugin_returns_none_after_first_turn_without_query(mem):
    mem.remember(
        "Perry is the operator",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )

    assert build_memory_context(mem, is_first_turn=False) is None


def test_plugin_later_turn_retrieves_relevant_evidence(mem):
    mem.remember(
        "Hermes should use query-aware retrieval for later turns",
        authority_class="evidence",
        source="conversation",
        confidence=0.9,
    )
    mem.remember(
        "Unrelated memory about database snapshots",
        authority_class="evidence",
        source="conversation",
        confidence=0.9,
    )

    context = build_memory_context(
        mem,
        is_first_turn=False,
        user_message="How should later turns retrieve memory?",
    )

    assert context is not None
    assert "query-aware retrieval" in context
    assert "database snapshots" not in context
    assert "## Preferences" not in context
    assert "## Lessons" not in context


def test_plugin_later_turn_keeps_identity_floor(mem):
    mem.remember(
        "Perry is the operator",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )

    context = build_memory_context(
        mem,
        is_first_turn=False,
        user_message="unknown topic",
    )

    assert context is not None
    assert "Perry is the operator" in context


def test_plugin_later_turn_budget_bounds_retrieved_evidence(mem):
    mem.remember(
        "alpha retrieval first relevant memory",
        authority_class="evidence",
        source="conversation",
        confidence=0.9,
    )
    mem.remember(
        "alpha retrieval second relevant memory",
        authority_class="evidence",
        source="conversation",
        confidence=0.9,
    )

    context = build_memory_context(
        mem,
        is_first_turn=False,
        user_message="alpha retrieval",
        budgets=minimal_budgets(
            identity={"limit": 0},
            evidence={"limit": 10, "max_chars": 39},
            preference={"limit": 0},
            lessons={"limit": 0},
        ),
    )

    assert context is not None
    assert context.count("- ") == 1


def test_extract_user_message_accepts_common_hook_shapes():
    assert _extract_user_message({"user_message": "hello"}) == "hello"
    assert _extract_user_message({"message": {"content": "from dict"}}) == "from dict"
    assert _extract_user_message({"prompt": "from prompt"}) == "from prompt"
    assert _extract_user_message({
        "messages": [
            {"role": "system", "content": "ignore"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "latest"},
        ]
    }) == "latest"


_REQUIRED_KEYS = {"available", "src_path", "db_path", "error"}


def test_memory_status_never_raises():
    result = memory_status()
    assert isinstance(result, dict)
    assert _REQUIRED_KEYS <= result.keys()


def test_memory_status_available_shape():
    result = memory_status()
    assert isinstance(result, dict)
    assert _REQUIRED_KEYS <= result.keys()
    assert isinstance(result["available"], bool)
    assert isinstance(result["src_path"], str)
    assert result["available"] is True
    assert result["error"] is None


def test_context_never_contains_error_text(mem):
    mem.remember(
        "Perry is the operator",
        authority_class="identity",
        source="observation",
        confidence=1.0,
    )

    context = build_memory_context(mem, is_first_turn=True)

    if context is not None:
        lower = context.lower()
        assert "error" not in lower
        assert "traceback" not in lower
        assert "failed" not in lower
        assert "import" not in lower
