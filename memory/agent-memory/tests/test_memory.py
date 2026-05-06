"""
Tests für AgentMemory — analog zu Lenas pytest 6/6
"""

import sys
import pytest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from memory import AgentMemory, AUTHORITY_POLICY, REBOUND_MAX_FACTS_AFTER_IDLE


@pytest.fixture
def mem(tmp_path):
    """In-Memory DB für jeden Test."""
    return AgentMemory(db_path=":memory:")


# ==================== TEST 1: Basis remember/recall ====================

def test_remember_and_recall(mem):
    fact_id = mem.remember(
        "Perry ist der Username",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )
    assert fact_id is not None

    facts = mem.recall("Username")
    assert len(facts) >= 1
    assert any("Perry" in f.content for f in facts)


# ==================== TEST 2: Authority Policy — authorization nur aus observation ====================

def test_authorization_rejected_from_conversation(mem):
    """authorization aus conversation wird stumm verworfen."""
    result = mem.remember(
        "Darf sudo ausführen",
        authority_class="authorization",
        source="conversation",   # nicht erlaubt für authorization
        confidence=1.0
    )
    assert result is None


def test_authorization_accepted_from_observation(mem):
    """authorization aus observation ist erlaubt."""
    result = mem.remember(
        "Darf sudo ausführen",
        authority_class="authorization",
        source="observation",
        confidence=1.0
    )
    assert result is not None


# ==================== TEST 3: Confidence-Filter ====================

def test_low_confidence_rejected(mem):
    """Zu niedriger Confidence-Wert für die Klasse wird verworfen."""
    # evidence braucht min 0.5
    result = mem.remember(
        "Vielleicht mag er Dunkelmodus",
        authority_class="evidence",
        source="inference",
        confidence=0.3  # unter 0.5
    )
    assert result is None


def test_sufficient_confidence_accepted(mem):
    result = mem.remember(
        "Vielleicht mag er Dunkelmodus",
        authority_class="preference",
        source="conversation",
        confidence=0.4  # preference braucht nur 0.3
    )
    assert result is not None


# ==================== TEST 4: Rebound-Protection ====================

def test_rebound_cap_enforced(mem):
    """Nach Aktivierung von Rebound: max N Facts, identity immer erlaubt."""
    mem._rebound_active = True
    mem._session_write_count = 0

    results = []
    for i in range(REBOUND_MAX_FACTS_AFTER_IDLE + 2):
        r = mem.remember(
            f"Fakt Nummer {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.8
        )
        results.append(r)

    accepted = [r for r in results if r is not None]
    assert len(accepted) == REBOUND_MAX_FACTS_AFTER_IDLE


def test_rebound_identity_always_passes(mem):
    """identity-Klasse passiert immer, auch im Rebound-Modus."""
    mem._rebound_active = True
    mem._session_write_count = REBOUND_MAX_FACTS_AFTER_IDLE  # Cap schon erreicht

    result = mem.remember(
        "Perry ist der Username",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )
    assert result is not None  # Floor — immer erlaubt


# ==================== TEST 5: forget_stale klassenspezifisch ====================

def test_forget_stale_respects_classes(mem):
    """identity wird nie gelöscht, preference schon wenn abgelaufen."""
    # identity-Fakt — soll überleben
    mem.remember(
        "Perry ist der Username",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )

    # preference-Fakt direkt mit expires_in_days=0 anlegen (sofort abgelaufen)
    from datetime import timedelta
    fact_id = mem.remember(
        "Mag Dunkelmodus",
        authority_class="preference",
        source="conversation",
        confidence=0.8,
        expires_in_days=1
    )
    assert fact_id is not None

    # Manuell expires_at in die Vergangenheit setzen — shared connection nutzen
    conn = mem._shared_conn if mem._shared_conn else sqlite3.connect(mem.db_path)
    should_close = mem._shared_conn is None
    cursor = conn.cursor()
    past = (datetime.utcnow() - timedelta(days=20)).isoformat()
    cursor.execute(
        "UPDATE facts SET last_accessed = ?, expires_at = ? WHERE id = ?",
        (past, past, fact_id)
    )
    conn.commit()
    if should_close:
        conn.close()

    deleted = mem.forget_stale()

    # identity überlebt
    identity_facts = mem.recall_by_authority("identity")
    assert len(identity_facts) >= 1

    # preference wurde gelöscht
    assert deleted.get("preference", 0) >= 1


# ==================== TEST 6: Lektionen und Entities ====================

def test_lessons_and_entities(mem):
    """Lessons und Entities funktionieren wie bei Lena."""
    lid = mem.learn(
        action="Dashboard ohne Fallback deployed",
        context="hermes-setup",
        outcome="negative",
        insight="Immer Fallback-Provider konfigurieren"
    )
    assert lid is not None

    lessons = mem.get_lessons(context="hermes-setup", outcome="negative")
    assert len(lessons) == 1
    assert "Fallback" in lessons[0].insight

    eid = mem.track_entity("Perry", "person", {
        "username": "xPerryx",
        "language": "de",
        "style": "direkt"
    })
    assert eid is not None

    entity = mem.get_entity("Perry", "person")
    assert entity is not None
    assert entity.attributes["username"] == "xPerryx"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
