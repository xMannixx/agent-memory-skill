"""
Tests für AgentMemory — analog zu Lenas pytest 6/6
"""

import sys
import sqlite3
import hashlib
import pytest
from types import MappingProxyType
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from memory import (
    AgentMemory,
    AUTHORITY_POLICY,
    REBOUND_MAX_FACTS_AFTER_IDLE,
    ANOMALY_WRITES_PER_MINUTE,
    STATS_LATENCY_WINDOW,
)


@pytest.fixture
def mem(tmp_path):
    """In-Memory DB für jeden Test."""
    return AgentMemory(db_path=":memory:")


@pytest.fixture
def file_mem(tmp_path):
    """File-backed DB Fixture fuer Tests, die echtes Disk-Verhalten brauchen."""
    return AgentMemory(db_path=str(tmp_path / "memory.db"))


class FrozenAgentMemory(AgentMemory):
    def __init__(self, db_path: str, frozen_now: datetime):
        self._frozen_now = frozen_now
        super().__init__(db_path=db_path)

    def _utc_now(self) -> datetime:
        return self._frozen_now

    def set_now(self, frozen_now: datetime):
        self._frozen_now = frozen_now


@pytest.fixture
def frozen_mem(tmp_path):
    """File-backed DB mit kontrollierbarer Uhr fuer TTL/Rebound-Tests."""
    return FrozenAgentMemory(
        db_path=str(tmp_path / "frozen.db"),
        frozen_now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )


def create_legacy_db_without_authority(db_path: Path):
    """Simuliert ein altes Schema vor authority_class und memory_meta."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE facts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT,
            source TEXT DEFAULT 'conversation',
            confidence REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            last_accessed TEXT NOT NULL,
            access_count INTEGER DEFAULT 1,
            expires_at TEXT,
            superseded_by TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO facts (
            id, content, tags, source, confidence, created_at,
            last_accessed, access_count, expires_at, superseded_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "legacy-1",
        "Legacy Fact",
        '["legacy"]',
        "conversation",
        0.9,
        "2026-01-01T12:00:00+00:00",
        "2026-01-01T12:00:00+00:00",
        1,
        None,
        None,
    ))
    cursor.execute("""
        CREATE TABLE session_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_write TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO session_log (last_write) VALUES (?)",
        ("2026-01-01T12:00:00+00:00",)
    )
    conn.commit()
    conn.close()


def create_legacy_lifecycle_db(db_path: Path):
    """Simuliert altes Lessons/Entities-Schema ohne Lifecycle-Spalten."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE lessons (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            context TEXT NOT NULL,
            outcome TEXT NOT NULL,
            insight TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        INSERT INTO lessons (
            id, action, context, outcome, insight, created_at, applied_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "legacy-lesson",
        "Legacy Action",
        "legacy-context",
        "positive",
        "Legacy Insight",
        "2026-01-01T12:00:00+00:00",
        2,
    ))
    cursor.execute("""
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            attributes TEXT,
            first_seen TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            fact_ids TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO entities (
            id, name, entity_type, attributes, first_seen, last_updated, fact_ids
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "legacy-entity",
        "Hermes",
        "agent",
        '{"role": "assistant"}',
        "2026-01-01T12:00:00+00:00",
        "2026-01-02T12:00:00+00:00",
        '["fact-1"]',
    ))
    conn.commit()
    conn.close()


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


def test_get_lessons_treats_percent_and_underscore_literally(mem):
    """LIKE-Suche escaped Nutzerinput statt %/_ als Wildcards zu interpretieren."""
    mem.learn(
        action="Wildcard test",
        context="literal_100%_ctx",
        outcome="neutral",
        insight="Soll gefunden werden"
    )
    mem.learn(
        action="Wildcard test",
        context="literalX100Y_ctx",
        outcome="neutral",
        insight="Soll nicht gefunden werden"
    )

    lessons = mem.get_lessons(context="literal_100%_ctx")

    assert len(lessons) == 1
    assert lessons[0].context == "literal_100%_ctx"


def test_track_entity_merges_attributes(mem):
    """track_entity ersetzt bestehende Attribute nicht mehr komplett."""
    entity_id = mem.track_entity("Perry", "person", {
        "username": "xPerryx",
        "language": "de",
    })
    again_id = mem.track_entity("Perry", "person", {
        "style": "direkt",
    })

    entity = mem.get_entity("Perry", "person")

    assert again_id == entity_id
    assert entity.attributes == {
        "username": "xPerryx",
        "language": "de",
        "style": "direkt",
    }


def test_update_entity_returns_updated_entity_without_extra_lookup(mem):
    """update_entity merged Attribute und gibt direkt die aktualisierte Entity zurueck."""
    mem.track_entity("Hermes", "agent", {"role": "assistant"})

    updated = mem.update_entity("Hermes", "agent", {"memory": "sqlite"})

    assert updated is not None
    assert updated.attributes == {"role": "assistant", "memory": "sqlite"}


def test_authority_policy_is_immutable():
    """Authority Policy ist gegen versehentliche Laufzeitmutation geschuetzt."""
    assert isinstance(AUTHORITY_POLICY, MappingProxyType)
    assert isinstance(AUTHORITY_POLICY["evidence"], MappingProxyType)
    with pytest.raises(TypeError):
        AUTHORITY_POLICY["new"] = {}
    with pytest.raises(TypeError):
        AUTHORITY_POLICY["evidence"]["min_confidence"] = 0.0


def test_forget_audit_includes_removed_count(mem):
    """forget-Audit zeigt, ob tatsaechlich ein Fact geloescht wurde."""
    fact_id = mem.remember(
        "Wird geloescht",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    mem.forget(fact_id)
    mem.forget("does-not-exist")

    entries = mem.get_audit(op="forget", limit=10)
    removed_by_id = {e["fact_id"]: e["metadata"]["removed"] for e in entries}

    assert removed_by_id[fact_id] == 1
    assert removed_by_id["does-not-exist"] == 0


def test_supersede_audit_includes_old_exists(mem):
    """supersede-Audit markiert, ob der alte Fact wirklich existierte."""
    new_id = mem.supersede(
        "does-not-exist",
        "Neuer Fact ohne alten Vorganger",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    entry = mem.get_audit(op="supersede", limit=1)[0]

    assert new_id is not None
    assert entry["metadata"]["old_exists"] is False


# ==================== TEST 6b: Lessons/Entities Lifecycle ====================

def test_migrates_legacy_lesson_entity_lifecycle_schema(tmp_path):
    """Alte Lessons/Entities-Schemas bekommen Lifecycle-Spalten ohne Datenverlust."""
    db_path = tmp_path / "legacy-lifecycle.db"
    create_legacy_lifecycle_db(db_path)

    migrated = AgentMemory(db_path=str(db_path))
    lessons = migrated.get_lessons(context="legacy-context")
    entity = migrated.get_entity("Hermes", "agent")

    assert len(lessons) == 1
    assert lessons[0].last_accessed is not None
    assert lessons[0].expires_at is not None
    assert lessons[0].applied_count == 2
    assert entity is not None
    assert entity.last_accessed is not None
    assert entity.expires_at is not None
    assert entity.attributes["role"] == "assistant"


def test_apply_lesson_refreshes_lifecycle(frozen_mem):
    """apply_lesson bumped applied_count und verlaengert last_accessed/expires_at."""
    lesson_id = frozen_mem.learn(
        "Action",
        "context",
        "positive",
        "Insight"
    )
    frozen_mem.set_now(datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc))

    frozen_mem.apply_lesson(lesson_id)
    lesson = frozen_mem.get_lessons(context="context")[0]

    assert lesson.applied_count == 1
    assert lesson.last_accessed == datetime(
        2026, 2, 1, 12, 0, tzinfo=timezone.utc
    ).isoformat()
    assert lesson.expires_at == datetime(
        2026, 7, 31, 12, 0, tzinfo=timezone.utc
    ).isoformat()


def test_stale_lifecycle_removes_expired_lesson(mem):
    """Expired Lessons werden vom Lifecycle-Cleanup entfernt."""
    lesson_id = mem.learn("Old", "ctx", "neutral", "old insight")
    conn = mem._shared_conn
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE lessons SET expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), lesson_id)
    )
    conn.commit()

    deleted = mem.forget_stale_lifecycle()

    assert deleted["lessons"] == 1
    assert mem.get_lessons(context="ctx") == []


def test_active_lesson_survives_cleanup_after_apply(frozen_mem):
    """Aktiv genutzte Lesson wird vor Cleanup wieder verlaengert."""
    lesson_id = frozen_mem.learn("Keep", "ctx", "positive", "keep")
    conn, should_close = frozen_mem._connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE lessons SET expires_at = ? WHERE id = ?",
        ("2025-12-31T12:00:00+00:00", lesson_id)
    )
    conn.commit()

    frozen_mem.apply_lesson(lesson_id)
    deleted = frozen_mem.forget_stale_lifecycle()
    lessons = frozen_mem.get_lessons(context="ctx")

    if should_close:
        conn.close()
    assert deleted["lessons"] == 0
    assert len(lessons) == 1


def test_stale_lifecycle_removes_expired_entity(mem):
    """Expired Entities werden vom Lifecycle-Cleanup entfernt."""
    mem.track_entity("OldEntity", "system", {"state": "old"})
    entity = mem.get_entity("OldEntity", "system")
    conn = mem._shared_conn
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE entities SET expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), entity.id)
    )
    conn.commit()

    deleted = mem.forget_stale_lifecycle()

    assert deleted["entities"] == 1
    assert mem.get_entity("OldEntity", "system") is None


def test_active_entity_survives_cleanup_after_update(frozen_mem):
    """Aktive Entity wird durch Update/Access vor Cleanup verlaengert."""
    frozen_mem.track_entity("Hermes", "agent", {"role": "assistant"})
    entity = frozen_mem.get_entity("Hermes", "agent")
    conn, should_close = frozen_mem._connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE entities SET expires_at = ? WHERE id = ?",
        ("2025-12-31T12:00:00+00:00", entity.id)
    )
    conn.commit()

    frozen_mem.update_entity("Hermes", "agent", {"memory": "sqlite"})
    deleted = frozen_mem.forget_stale_lifecycle()
    entity = frozen_mem.get_entity("Hermes", "agent")

    if should_close:
        conn.close()
    assert deleted["entities"] == 0
    assert entity is not None
    assert entity.attributes["memory"] == "sqlite"


def test_lifecycle_cleanup_audits_removed_counts(mem):
    """Lifecycle-Cleanup schreibt Audit mit geloeschten Counts."""
    lesson_id = mem.learn("Audit", "life", "neutral", "audit")
    mem.track_entity("AuditEntity", "system", {})
    entity = mem.get_entity("AuditEntity", "system")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = mem._shared_conn
    cursor = conn.cursor()
    cursor.execute("UPDATE lessons SET expires_at = ? WHERE id = ?", (past, lesson_id))
    cursor.execute("UPDATE entities SET expires_at = ? WHERE id = ?", (past, entity.id))
    conn.commit()

    deleted = mem.forget_stale_lifecycle()
    audit = mem.get_audit(op="forget_stale_lifecycle", limit=10)

    assert deleted == {"lessons": 1, "entities": 1}
    assert {e["metadata"]["type"] for e in audit} == {"lessons", "entities"}


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


# ==================== TEST 7b: Test-Infrastruktur und Migrationen ====================

@pytest.mark.parametrize(
    "authority_class,source,confidence,expected",
    [
        ("identity", "observation", 1.0, True),
        ("identity", "conversation", 0.95, True),
        ("identity", "inference", 1.0, False),
        ("preference", "conversation", 0.3, True),
        ("preference", "conversation", 0.29, False),
        ("evidence", "inference", 0.5, True),
        ("evidence", "conversation", 0.49, False),
        ("authorization", "observation", 0.9, True),
        ("authorization", "conversation", 1.0, False),
    ],
)
def test_authority_policy_accept_reject_matrix(
    mem, authority_class, source, confidence, expected
):
    """Policy-Matrix prueft erlaubte Quellen und Confidence-Grenzen."""
    result = mem.remember(
        f"Policy Matrix {authority_class} {source} {confidence}",
        authority_class=authority_class,
        source=source,
        confidence=confidence
    )
    assert (result is not None) is expected


def test_file_backed_db_persists_across_instances(tmp_path):
    """File-backed Daten bleiben ueber neue AgentMemory-Instanzen erhalten."""
    db_path = tmp_path / "persistent.db"
    first = AgentMemory(db_path=str(db_path))
    fact_id = first.remember(
        "Persistiert ueber Instanzen",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )

    second = AgentMemory(db_path=str(db_path))
    fact = second.get_fact(fact_id)

    assert fact is not None
    assert fact.content == "Persistiert ueber Instanzen"


def test_frozen_time_controls_fact_ttl(frozen_mem):
    """FrozenAgentMemory erlaubt TTL-Tests ohne manuelles SQL-Patching."""
    fact_id = frozen_mem.remember(
        "Zeitgesteuerter Fact",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    fact = frozen_mem.get_fact(fact_id)

    assert fact.expires_at == datetime(
        2026, 1, 15, 12, 0, tzinfo=timezone.utc
    ).isoformat()


def test_frozen_time_controls_rebound_detection(tmp_path):
    """Rebound kann ueber kontrollierte Zeit und memory_meta getestet werden."""
    db_path = tmp_path / "rebound.db"
    first = FrozenAgentMemory(
        db_path=str(db_path),
        frozen_now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    first.remember(
        "Baseline",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    second = FrozenAgentMemory(
        db_path=str(db_path),
        frozen_now=datetime(2026, 1, 1, 19, 1, tzinfo=timezone.utc),
    )

    assert second._rebound_active is True


def test_migrates_legacy_fact_schema_without_losing_rows(tmp_path):
    """Alte DB ohne authority_class wird migriert und bleibt lesbar."""
    db_path = tmp_path / "legacy.db"
    create_legacy_db_without_authority(db_path)

    migrated = AgentMemory(db_path=str(db_path))
    fact = migrated.get_fact("legacy-1")

    assert fact is not None
    assert fact.content == "Legacy Fact"
    assert fact.authority_class == "evidence"

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(facts)")
    columns = [row[1] for row in cursor.fetchall()]
    cursor.execute(
        "SELECT value FROM memory_meta WHERE key = 'last_write'"
    )
    last_write = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type = 'table' AND name = 'session_log'
    """)
    session_log_count = cursor.fetchone()[0]
    conn.close()

    assert "authority_class" in columns
    assert last_write == "2026-01-01T12:00:00+00:00"
    assert session_log_count == 0


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


# ==================== TEST 9: Audit-Log ====================

def test_audit_logs_successful_write(mem):
    """Jeder erfolgreiche remember-Insert produziert einen audit-Eintrag op=write."""
    fact_id = mem.remember(
        "Audit-relevanter Fakt",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    entries = mem.get_audit(op="write")
    assert any(e["fact_id"] == fact_id and e["accepted"] for e in entries)


def test_audit_logs_policy_reject_with_reason(mem):
    """Source- und Confidence-Rejects landen mit Grund im Audit-Log."""
    mem.remember(
        "Heimlich",
        authority_class="authorization",
        source="conversation",
        confidence=1.0
    )
    mem.remember(
        "Zu unsicher",
        authority_class="evidence",
        source="conversation",
        confidence=0.1
    )
    rejects = mem.get_audit(op="policy_reject")
    reasons = {e["reason"] for e in rejects}
    assert {"source_not_allowed", "low_confidence"}.issubset(reasons)
    assert all(not e["accepted"] for e in rejects)


def test_audit_logs_forget_and_supersede(mem):
    """forget und supersede schreiben passende audit-Zeilen."""
    fact_id = mem.remember(
        "Wird ersetzt",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    new_id = mem.supersede(
        fact_id,
        "Neuer Wert",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    mem.forget(new_id)

    ops = {e["op"] for e in mem.get_audit(limit=50)}
    assert {"supersede", "forget"}.issubset(ops)

    supersede_entries = mem.get_audit(op="supersede")
    assert supersede_entries[0]["metadata"]["old_id"] == fact_id
    assert supersede_entries[0]["metadata"]["new_id"] == new_id


def test_audit_get_filter_by_op(mem):
    """get_audit(op=...) filtert exakt."""
    mem.remember("Fakt A", authority_class="evidence",
                 source="conversation", confidence=0.9)
    mem.remember("Fakt B", authority_class="evidence",
                 source="conversation", confidence=0.9)
    writes = mem.get_audit(op="write")
    assert all(e["op"] == "write" for e in writes)
    assert len(writes) >= 2


def test_audit_includes_content_hash(mem):
    """Audit speichert content_hash separat von fact_id."""
    mem.remember("Hashbar", authority_class="evidence",
                 source="conversation", confidence=0.9)
    entries = mem.get_audit(op="write")
    expected = hashlib.sha256(b"Hashbar").hexdigest()
    assert entries[0]["content_hash"] == expected


# ==================== TEST 10: Snapshots / Restore ====================

def test_snapshot_creates_file_with_metadata(file_mem):
    """snapshot() legt eine Datei an, list_snapshots zeigt sie."""
    file_mem.remember("Persistenter Fakt", authority_class="identity",
                      source="observation", confidence=1.0)
    path = file_mem.snapshot(label="manual")
    assert Path(path).is_file()

    snaps = file_mem.list_snapshots()
    paths = [s["path"] for s in snaps]
    assert path in paths
    target = next(s for s in snaps if s["path"] == path)
    assert target["size_bytes"] > 0


def test_snapshot_restore_roundtrip(file_mem):
    """Nach Restore tauchen geloeschte Facts wieder auf."""
    fact_id = file_mem.remember(
        "Wird geloescht und wiederhergestellt",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    snap_path = file_mem.snapshot(label="before-delete")

    file_mem.forget(fact_id)
    assert file_mem.get_fact(fact_id) is None

    file_mem.restore(snap_path)

    restored = file_mem.get_fact(fact_id)
    assert restored is not None
    assert restored.content == "Wird geloescht und wiederhergestellt"


def test_restore_creates_pre_restore_backup(file_mem):
    """restore() erzeugt vorher einen pre-restore-Auto-Snapshot."""
    file_mem.remember("Original", authority_class="identity",
                      source="observation", confidence=1.0)
    snap_path = file_mem.snapshot(label="first")

    before_count = len(file_mem.list_snapshots())
    file_mem.restore(snap_path)
    after = file_mem.list_snapshots()
    assert len(after) >= before_count + 1
    labels = " ".join(s["label"] for s in after)
    assert "pre-restore" in labels


# ==================== TEST 11: Anomaly Detection ====================

def test_anomaly_detected_on_burst(mem):
    """Mehr Writes als ANOMALY_WRITES_PER_MINUTE in 60s loggt eine Anomalie."""
    for i in range(ANOMALY_WRITES_PER_MINUTE + 5):
        mem.remember(
            f"Burst Fakt {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )
    anomalies = mem.anomalies()
    assert len(anomalies) >= 1
    assert anomalies[0]["op"] == "anomaly_detected"


def test_anomalies_api_returns_recent(mem):
    """anomalies() liefert nur op=anomaly_detected, neueste zuerst."""
    for i in range(ANOMALY_WRITES_PER_MINUTE + 2):
        mem.remember(
            f"Spam {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )
    anomalies = mem.anomalies(limit=5)
    assert anomalies, "Erwartete mindestens eine Anomalie"
    assert all(a["op"] == "anomaly_detected" for a in anomalies)


def test_anomaly_does_not_block_writes(mem):
    """Anomalie ist Telemetrie — Schreiben funktioniert weiter."""
    last_id = None
    for i in range(ANOMALY_WRITES_PER_MINUTE + 3):
        last_id = mem.remember(
            f"Weiter geht es {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )
    assert last_id is not None
    assert mem.get_fact(last_id) is not None


# ==================== TEST 12: Observability Stats ====================

def test_stats_tracks_recall_count(mem):
    mem.remember(
        "Recall Count Test",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    mem.recall("Recall")
    mem.recall("Count")
    mem.recall_by_authority("evidence")

    assert mem.stats()["recalls"] == 3


def test_stats_records_recall_latency(mem):
    mem.remember(
        "Latency Test",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    mem.recall("Latency")
    latency = mem.stats()["recall_latency_ms"]

    assert latency["count"] == 1
    assert latency["avg"] >= 0
    assert latency["p50"] >= 0
    assert latency["p95"] >= 0
    assert latency["max"] >= 0


def test_stats_latency_window_is_bounded(mem):
    for i in range(STATS_LATENCY_WINDOW + 5):
        mem._record_recall(float(i))

    latency = mem.stats()["recall_latency_ms"]

    assert mem.stats()["recalls"] == STATS_LATENCY_WINDOW + 5
    assert latency["count"] == STATS_LATENCY_WINDOW
    assert mem._recall_latency_ms[0] == 5.0


def test_stats_reports_stale_facts(mem):
    fact_id = mem.remember(
        "Stale Fact",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    cursor = mem._shared_conn.cursor()
    cursor.execute(
        "UPDATE facts SET expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), fact_id)
    )
    mem._shared_conn.commit()

    stats = mem.stats()

    assert stats["stale_facts"] == 1
    assert stats["stale_ratio"] == 1.0


def test_stats_reports_superseded_ratio(mem):
    fact_id = mem.remember(
        "Alter Fakt",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )
    mem.supersede(
        fact_id,
        "Neuer Fakt",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    stats = mem.stats()

    assert stats["superseded_facts"] == 1
    assert stats["total_facts"] == 2
    assert stats["superseded_ratio"] == 0.5


def test_stats_by_class_ratio_sums_to_one(mem):
    mem.remember(
        "Identity Fact",
        authority_class="identity",
        source="observation",
        confidence=1.0
    )
    mem.remember(
        "Preference Fact",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    mem.remember(
        "Evidence Fact",
        authority_class="evidence",
        source="conversation",
        confidence=0.9
    )

    ratios = mem.stats()["by_class_ratio"]

    assert pytest.approx(sum(ratios.values())) == 1.0
    assert ratios["identity"] == pytest.approx(1 / 3)
    assert ratios["preference"] == pytest.approx(1 / 3)
    assert ratios["evidence"] == pytest.approx(1 / 3)


def test_stats_empty_db_latency_is_safe(mem):
    stats = mem.stats()

    assert stats["recalls"] == 0
    assert stats["recall_latency_ms"] == {
        "count": 0,
        "avg": None,
        "p50": None,
        "p95": None,
        "max": None,
    }
    assert stats["stale_ratio"] == 0.0
    assert stats["superseded_ratio"] == 0.0


# ==================== TEST 13: Confidence Decay ====================

def test_effective_confidence_decays_over_time(frozen_mem):
    last_accessed = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat()
    frozen_mem.set_now(datetime(2026, 1, 8, 12, 0, tzinfo=timezone.utc))

    effective = frozen_mem._effective_confidence(
        0.8,
        last_accessed,
        "preference"
    )

    assert effective == pytest.approx(0.4)


def test_identity_confidence_does_not_decay(frozen_mem):
    last_accessed = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat()
    frozen_mem.set_now(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))

    effective = frozen_mem._effective_confidence(
        0.95,
        last_accessed,
        "identity"
    )

    assert effective == 0.95


def test_recall_filters_faded_facts(frozen_mem):
    fact_id = frozen_mem.remember(
        "Fading Memory",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )
    frozen_mem.set_now(datetime(2026, 2, 12, 12, 0, tzinfo=timezone.utc))

    facts = frozen_mem.recall("Fading", min_confidence=0.3)

    assert fact_id is not None
    assert facts == []


def test_recall_keeps_fresh_facts(frozen_mem):
    fact_id = frozen_mem.remember(
        "Fresh Memory",
        authority_class="preference",
        source="conversation",
        confidence=0.9
    )

    facts = frozen_mem.recall("Fresh", min_confidence=0.3)

    assert any(f.id == fact_id for f in facts)


def test_decay_respects_min_confidence_limit(frozen_mem):
    for i in range(5):
        frozen_mem.remember(
            f"Limit Decay Candidate {i}",
            authority_class="evidence",
            source="conversation",
            confidence=0.9
        )
    conn, should_close = frozen_mem._connect()
    cursor = conn.cursor()
    old_access = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat()
    cursor.execute("""
        UPDATE facts
        SET last_accessed = ?
        WHERE content LIKE 'Limit Decay Candidate 0%'
        OR content LIKE 'Limit Decay Candidate 1%'
    """, (old_access,))
    conn.commit()
    if should_close:
        conn.close()

    facts = frozen_mem.recall("Limit", limit=2, min_confidence=0.5)

    assert len(facts) == 2
    assert all("Candidate 0" not in f.content for f in facts)
    assert all("Candidate 1" not in f.content for f in facts)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
