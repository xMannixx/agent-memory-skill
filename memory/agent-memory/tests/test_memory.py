"""
Tests für AgentMemory — analog zu Lenas pytest 6/6
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from memory import AgentMemory, AUTHORITY_POLICY, REBOUND_MAX_FACTS_AFTER_IDLE


@pytest.fixture
def mem(tmp_path):
    """In-Memory DB für jeden Test."""
    return AgentMemory(db_path=":memory:")


@pytest.fixture
def file_mem(tmp_path):
    """File-backed DB Fixture fuer Tests, die echtes Disk-Verhalten brauchen."""
    return AgentMemory(db_path=str(tmp_path / "memory.db"))


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

    # preference-Fakt direkt mit kurzer TTL anlegen und dann manuell ablaufen lassen
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
    past = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
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


def test_recall_by_authority_touches_access_metadata(mem):
    """Plugin-Hauptpfad aktualisiert last_accessed und verlängert Sliding-TTL."""
    fact_id = mem.remember(
        "Mag kurze direkte Antworten",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    assert fact_id is not None

    conn = mem._shared_conn if mem._shared_conn else sqlite3.connect(mem.db_path)
    should_close = mem._shared_conn is None
    cursor = conn.cursor()
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    old_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cursor.execute(
        "UPDATE facts SET last_accessed = ?, expires_at = ? WHERE id = ?",
        (past, old_expiry, fact_id)
    )
    conn.commit()

    facts = mem.recall_by_authority("preference")
    assert any(f.id == fact_id for f in facts)

    cursor.execute(
        "SELECT last_accessed, expires_at, access_count FROM facts WHERE id = ?",
        (fact_id,)
    )
    last_accessed, expires_at, access_count = cursor.fetchone()
    if should_close:
        conn.close()

    assert datetime.fromisoformat(last_accessed) > datetime.fromisoformat(past)
    assert datetime.fromisoformat(expires_at) > datetime.fromisoformat(old_expiry)
    assert access_count == 2


def test_forget_removes_fact_from_fts(mem):
    """FTS bleibt nach einem delete synchron und liefert keine orphaned Treffer."""
    fact_id = mem.remember(
        "Einzigartiger FTS Loeschtest",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    assert fact_id is not None
    assert len(mem.recall("Loeschtest")) == 1

    mem.forget(fact_id)

    assert mem.recall("Loeschtest") == []


def test_log_write_uses_single_memory_meta_row(mem):
    """Write-Tracking nutzt eine einzelne memory_meta-Zeile statt wachsender Logs."""
    for i in range(3):
        fact_id = mem.remember(
            f"Session Meta Fakt {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )
        assert fact_id is not None

    conn = mem._shared_conn if mem._shared_conn else sqlite3.connect(mem.db_path)
    should_close = mem._shared_conn is None
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM memory_meta WHERE key = 'last_write'")
    last_write_rows = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'session_log'
    """)
    session_log_tables = cursor.fetchone()[0]
    if should_close:
        conn.close()

    assert last_write_rows == 1
    assert session_log_tables == 0


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


# ==================== TEST 7: Schema-Indexe und WAL ====================

EXPECTED_INDEXES = {
    "idx_facts_class_super",
    "idx_facts_expires",
    "idx_facts_last_accessed",
    "idx_lessons_outcome_time",
    "idx_entities_type_name",
}


def test_required_indexes_exist(mem):
    """Alle Performance-Indexe werden bei Init angelegt."""
    conn = mem._shared_conn
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    )
    found = {row[0] for row in cursor.fetchall()}
    missing = EXPECTED_INDEXES - found
    assert missing == set(), f"Fehlende Indexe: {missing}"


def test_wal_mode_on_file_db(file_mem):
    """File-backed DB laeuft im WAL-Modus fuer concurrent reader/writer."""
    conn = sqlite3.connect(file_mem.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_memory_db_does_not_use_wal(mem):
    """:memory: DB bleibt im Default-Journalmode, WAL wird uebersprungen."""
    cursor = mem._shared_conn.cursor()
    cursor.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0].lower()
    assert mode != "wal"


# ==================== TEST 8: Idempotente remember() ====================

def test_generate_id_stable_across_time(mem):
    """_generate_id liefert deterministisch denselben Hash."""
    a = mem._generate_id("Perry ist der Username", "identity")
    b = mem._generate_id("Perry ist der Username", "identity")
    assert a == b


def test_remember_same_content_returns_same_id(mem):
    """Doppeltes remember mit gleichem Text+Lane liefert dieselbe id und bumpt access_count."""
    first = mem.remember(
        "Mag kurze Antworten",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    second = mem.remember(
        "Mag kurze Antworten",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    assert first is not None
    assert first == second

    cursor = mem._shared_conn.cursor()
    cursor.execute(
        "SELECT COUNT(*), MAX(access_count) FROM facts WHERE id = ?",
        (first,)
    )
    count, access_count = cursor.fetchone()
    assert count == 1
    assert access_count >= 2


def test_remember_same_content_different_lane_distinct_ids(mem):
    """Gleicher Text in unterschiedlichen Lanes ergibt unterschiedliche Facts."""
    identity_id = mem.remember(
        "Lieblingsfarbe ist blau",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )
    preference_id = mem.remember(
        "Lieblingsfarbe ist blau",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    assert identity_id is not None
    assert preference_id is not None
    assert identity_id != preference_id


def test_remember_idempotent_does_not_consume_rebound_budget(mem):
    """Im Rebound-Modus zaehlt eine wiederholte Idempotenz nicht gegen den Cap."""
    mem._rebound_active = True
    mem._rebound_write_count = 0

    fact_id = None
    for _ in range(REBOUND_MAX_FACTS_AFTER_IDLE + 3):
        fact_id = mem.remember(
            "Selber Fakt immer wieder",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )

    assert fact_id is not None
    assert mem._rebound_write_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
