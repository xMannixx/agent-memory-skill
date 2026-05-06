"""
AgentMemory - Persistent Memory for Hermes Agent

Basiert auf Lena OpenClaw's agent-memory-1-0-0,
erweitert mit Authority Lanes und Rebound-Protection
aus den Moltbook signalfoundry/lucy17 Diskussionen.

MIT License
"""

import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict


# ==================== AUTHORITY POLICY ====================

AUTHORITY_POLICY = {
    "identity": {
        "ttl_days": None,           # Nie löschen — Floor
        "min_confidence": 0.9,
        "allowed_sources": ["observation", "conversation"],
    },
    "preference": {
        "ttl_days": 14,
        "min_confidence": 0.3,
        "allowed_sources": ["conversation", "observation"],
    },
    "evidence": {
        "ttl_days": 60,
        "min_confidence": 0.5,
        "allowed_sources": ["conversation", "observation", "inference"],
    },
    "authorization": {
        "ttl_days": 90,
        "min_confidence": 0.9,
        "allowed_sources": ["observation"],  # NICHT aus conversation
    },
}

REBOUND_IDLE_THRESHOLD_HOURS = 6
REBOUND_MAX_FACTS_AFTER_IDLE = 3


@dataclass
class Fact:
    id: str
    content: str
    tags: List[str]
    source: str
    confidence: float
    authority_class: str
    created_at: str
    last_accessed: str
    access_count: int
    expires_at: Optional[str] = None
    superseded_by: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Lesson:
    id: str
    action: str
    context: str
    outcome: str
    insight: str
    created_at: str
    applied_count: int = 0


@dataclass
class Entity:
    id: str
    name: str
    entity_type: str
    attributes: Dict[str, Any]
    first_seen: str
    last_updated: str
    fact_ids: List[str]


class AgentMemory:
    """
    Persistent memory system für Hermes Agent.

    Basiert auf Lenas agent-memory-1-0-0 mit:
    - Authority Lanes (identity/preference/evidence/authorization)
    - Rebound-Protection nach Idle-Phasen
    - Klassenspezifisches forget_stale()
    - Source-Trust-Hierarchie

    Usage:
        mem = AgentMemory()
        mem.remember("Perry ist der Username", authority_class="identity",
                     source="observation", confidence=1.0)
        facts = mem.recall("username")
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_dir = Path.home() / ".hermes" / "agent-memory"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "memory.db")

        self.db_path = db_path
        self._session_write_count = 0
        self._session_start = datetime.utcnow()
        self._rebound_active = False

        # Shared connection für :memory: (Tests) — SQLite in-memory
        # verliert Daten wenn Connection geschlossen wird
        self._shared_conn = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")

        self._init_db()
        self._check_rebound()

    def _connect(self):
        """Gibt (conn, should_close) zurück."""
        if self._shared_conn is not None:
            return self._shared_conn, False
        return sqlite3.connect(self.db_path), True

    def _init_db(self):
        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                tags TEXT,
                source TEXT DEFAULT 'conversation',
                confidence REAL DEFAULT 1.0,
                authority_class TEXT DEFAULT 'evidence',
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 1,
                expires_at TEXT,
                superseded_by TEXT
            )
        """)

        # Migration: authority_class hinzufügen falls nicht vorhanden
        cursor.execute("PRAGMA table_info(facts)")
        columns = [row[1] for row in cursor.fetchall()]
        if "authority_class" not in columns:
            cursor.execute(
                "ALTER TABLE facts ADD COLUMN authority_class TEXT DEFAULT 'evidence'"
            )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
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
            CREATE TABLE IF NOT EXISTS entities (
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
            CREATE TABLE IF NOT EXISTS session_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_start TEXT NOT NULL,
                last_write TEXT
            )
        """)

        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(content, tags, tokenize='porter')
        """)

        conn.commit()
        if should_close:
            conn.close()

    def _check_rebound(self):
        """
        Prüft ob Rebound-Modus aktiv.
        Nach >REBOUND_IDLE_THRESHOLD_HOURS ohne Write: Rebound aktiv.
        identity-Klasse (Floor) sinkt NIE ab.
        """
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_write FROM session_log ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if should_close:
            conn.close()

        if row and row[0]:
            last_write = datetime.fromisoformat(row[0])
            idle_hours = (datetime.utcnow() - last_write).total_seconds() / 3600
            if idle_hours > REBOUND_IDLE_THRESHOLD_HOURS:
                self._rebound_active = True

    def _log_write(self):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO session_log (session_start, last_write) VALUES (?, ?)",
            (self._session_start.isoformat(), self._now())
        )
        conn.commit()
        if should_close:
            conn.close()

    def _generate_id(self, content: str) -> str:
        timestamp = datetime.utcnow().isoformat()
        return hashlib.sha256(f"{content}{timestamp}".encode()).hexdigest()[:12]

    def _now(self) -> str:
        return datetime.utcnow().isoformat()

    def _row_to_fact(self, row) -> Fact:
        return Fact(
            id=row[0], content=row[1],
            tags=json.loads(row[2] or "[]"),
            source=row[3], confidence=row[4],
            authority_class=row[5],
            created_at=row[6], last_accessed=row[7],
            access_count=row[8], expires_at=row[9],
            superseded_by=row[10]
        )

    # ==================== FACTS ====================

    def remember(self, content: str, tags: List[str] = None,
                 source: str = "conversation", confidence: float = 1.0,
                 authority_class: str = "evidence",
                 expires_in_days: int = None) -> Optional[str]:
        """
        Speichert einen Fakt.

        Rebound-Protection: Nach Idle max REBOUND_MAX_FACTS_AFTER_IDLE Facts.
        identity ist Floor — immer erlaubt, kein Cap.
        authorization nur aus 'observation' erlaubt.
        """
        policy = AUTHORITY_POLICY.get(authority_class, AUTHORITY_POLICY["evidence"])

        # Source-Validierung
        if source not in policy["allowed_sources"]:
            return None

        # Confidence-Filter
        if confidence < policy["min_confidence"]:
            return None

        # Rebound-Protection (identity = Floor, immer erlaubt)
        if self._rebound_active and authority_class != "identity":
            if self._session_write_count >= REBOUND_MAX_FACTS_AFTER_IDLE:
                return None
            self._session_write_count += 1

        fact_id = self._generate_id(content)
        now = self._now()
        tags = tags or []

        if expires_in_days is None and policy["ttl_days"] is not None:
            expires_in_days = policy["ttl_days"]

        expires_at = None
        if expires_in_days:
            expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat()

        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO facts (id, content, tags, source, confidence,
                             authority_class, created_at, last_accessed,
                             access_count, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (fact_id, content, json.dumps(tags), source, confidence,
              authority_class, now, now, expires_at))

        cursor.execute("""
            INSERT INTO facts_fts (rowid, content, tags)
            SELECT rowid, content, tags FROM facts WHERE id = ?
        """, (fact_id,))

        conn.commit()
        if should_close:
            conn.close()

        self._log_write()
        return fact_id

    def recall(self, query: str, limit: int = 10,
               tags: List[str] = None, min_confidence: float = 0.3,
               authority_class: str = None) -> List[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = """
            SELECT f.* FROM facts f
            JOIN facts_fts fts ON f.rowid = fts.rowid
            WHERE facts_fts MATCH ?
            AND f.confidence >= ?
            AND (f.expires_at IS NULL OR f.expires_at > ?)
            AND f.superseded_by IS NULL
        """
        params = [query, min_confidence, self._now()]

        if authority_class:
            sql += " AND f.authority_class = ?"
            params.append(authority_class)

        sql += " ORDER BY fts.rank LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        facts = []

        for row in rows:
            fact = self._row_to_fact(row)
            if tags and not all(t in fact.tags for t in tags):
                continue
            facts.append(fact)
            cursor.execute("""
                UPDATE facts SET last_accessed = ?, access_count = access_count + 1
                WHERE id = ?
            """, (self._now(), fact.id))

        conn.commit()
        if should_close:
            conn.close()
        return facts

    def recall_by_authority(self, authority_class: str, limit: int = 50) -> List[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM facts
            WHERE authority_class = ?
            AND superseded_by IS NULL
            AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY last_accessed DESC LIMIT ?
        """, (authority_class, self._now(), limit))
        rows = cursor.fetchall()
        if should_close:
            conn.close()
        return [self._row_to_fact(r) for r in rows]

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM facts WHERE id = ?", (fact_id,))
        row = cursor.fetchone()
        if should_close:
            conn.close()
        return self._row_to_fact(row) if row else None

    def list_facts(self, tags: List[str] = None, limit: int = 50,
                   authority_class: str = None,
                   include_superseded: bool = False) -> List[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = "SELECT * FROM facts WHERE 1=1"
        params = []

        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        if authority_class:
            sql += " AND authority_class = ?"
            params.append(authority_class)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        if should_close:
            conn.close()

        facts = []
        for row in rows:
            fact = self._row_to_fact(row)
            if tags and not any(t in fact.tags for t in tags):
                continue
            facts.append(fact)
        return facts

    def supersede(self, old_fact_id: str, new_content: str, **kwargs) -> Optional[str]:
        new_id = self.remember(new_content, **kwargs)
        if not new_id:
            return None

        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE facts SET superseded_by = ? WHERE id = ?",
            (new_id, old_fact_id)
        )
        conn.commit()
        if should_close:
            conn.close()
        return new_id

    def forget(self, fact_id: str):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        conn.commit()
        if should_close:
            conn.close()

    def forget_stale(self) -> Dict[str, int]:
        """
        Klassenspezifisches Cleanup nach Policy-TTL.
        identity wird NIE gelöscht (Floor).
        """
        conn, should_close = self._connect()
        cursor = conn.cursor()
        deleted = {}

        for cls, policy in AUTHORITY_POLICY.items():
            if policy["ttl_days"] is None:
                deleted[cls] = 0
                continue

            cutoff = (datetime.utcnow() - timedelta(days=policy["ttl_days"])).isoformat()
            cursor.execute("""
                DELETE FROM facts
                WHERE authority_class = ?
                AND last_accessed < ?
                AND superseded_by IS NULL
            """, (cls, cutoff))
            deleted[cls] = cursor.rowcount

        cursor.execute("""
            DELETE FROM facts_fts WHERE rowid NOT IN (SELECT rowid FROM facts)
        """)

        conn.commit()
        if should_close:
            conn.close()
        return deleted

    # ==================== LESSONS ====================

    def learn(self, action: str, context: str, outcome: str, insight: str) -> str:
        lesson_id = self._generate_id(f"{action}{context}")
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO lessons (id, action, context, outcome, insight, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (lesson_id, action, context, outcome, insight, self._now()))
        conn.commit()
        if should_close:
            conn.close()
        return lesson_id

    def get_lessons(self, context: str = None, outcome: str = None,
                    limit: int = 10) -> List[Lesson]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = "SELECT * FROM lessons WHERE 1=1"
        params = []

        if context:
            sql += " AND context LIKE ?"
            params.append(f"%{context}%")
        if outcome:
            sql += " AND outcome = ?"
            params.append(outcome)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        if should_close:
            conn.close()

        return [
            Lesson(id=r[0], action=r[1], context=r[2],
                   outcome=r[3], insight=r[4], created_at=r[5],
                   applied_count=r[6])
            for r in rows
        ]

    def apply_lesson(self, lesson_id: str):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lessons SET applied_count = applied_count + 1 WHERE id = ?",
            (lesson_id,)
        )
        conn.commit()
        if should_close:
            conn.close()

    # ==================== ENTITIES ====================

    def track_entity(self, name: str, entity_type: str,
                     attributes: Dict[str, Any] = None) -> str:
        entity_id = self._generate_id(f"{entity_type}:{name}")
        now = self._now()
        attributes = attributes or {}

        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE entities SET attributes = ?, last_updated = ? WHERE id = ?",
                (json.dumps(attributes), now, existing[0])
            )
            entity_id = existing[0]
        else:
            cursor.execute("""
                INSERT INTO entities (id, name, entity_type, attributes,
                                     first_seen, last_updated, fact_ids)
                VALUES (?, ?, ?, ?, ?, ?, '[]')
            """, (entity_id, name, entity_type, json.dumps(attributes), now, now))

        conn.commit()
        if should_close:
            conn.close()
        return entity_id

    def get_entity(self, name: str, entity_type: str = None) -> Optional[Entity]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        if entity_type:
            cursor.execute(
                "SELECT * FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type)
            )
        else:
            cursor.execute("SELECT * FROM entities WHERE name = ?", (name,))

        row = cursor.fetchone()
        if should_close:
            conn.close()

        if not row:
            return None

        return Entity(
            id=row[0], name=row[1], entity_type=row[2],
            attributes=json.loads(row[3] or "{}"),
            first_seen=row[4], last_updated=row[5],
            fact_ids=json.loads(row[6] or "[]")
        )

    def update_entity(self, name: str, entity_type: str,
                      attributes: Dict[str, Any]) -> Optional[Entity]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, attributes FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        )
        row = cursor.fetchone()
        if not row:
            if should_close:
                conn.close()
            return None

        existing = json.loads(row[1] or "{}")
        existing.update(attributes)
        cursor.execute(
            "UPDATE entities SET attributes = ?, last_updated = ? WHERE id = ?",
            (json.dumps(existing), self._now(), row[0])
        )
        conn.commit()
        if should_close:
            conn.close()
        return self.get_entity(name, entity_type)

    def link_fact_to_entity(self, entity_name: str, fact_id: str):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, fact_ids FROM entities WHERE name = ?", (entity_name,))
        row = cursor.fetchone()
        if row:
            fact_ids = json.loads(row[1] or "[]")
            if fact_id not in fact_ids:
                fact_ids.append(fact_id)
                cursor.execute(
                    "UPDATE entities SET fact_ids = ? WHERE id = ?",
                    (json.dumps(fact_ids), row[0])
                )
        conn.commit()
        if should_close:
            conn.close()

    def list_entities(self, entity_type: str = None) -> List[Entity]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        if entity_type:
            cursor.execute(
                "SELECT * FROM entities WHERE entity_type = ? ORDER BY last_updated DESC",
                (entity_type,)
            )
        else:
            cursor.execute("SELECT * FROM entities ORDER BY last_updated DESC")

        rows = cursor.fetchall()
        if should_close:
            conn.close()
        return [
            Entity(id=r[0], name=r[1], entity_type=r[2],
                   attributes=json.loads(r[3] or "{}"),
                   first_seen=r[4], last_updated=r[5],
                   fact_ids=json.loads(r[6] or "[]"))
            for r in rows
        ]

    # ==================== UTILITIES ====================

    def stats(self) -> Dict[str, Any]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM facts WHERE superseded_by IS NULL")
        active = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM facts WHERE superseded_by IS NOT NULL")
        superseded = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM lessons")
        lessons = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM entities")
        entities = cursor.fetchone()[0]

        cursor.execute("""
            SELECT authority_class, COUNT(*)
            FROM facts WHERE superseded_by IS NULL
            GROUP BY authority_class
        """)
        by_class = {row[0]: row[1] for row in cursor.fetchall()}

        if should_close:
            conn.close()

        return {
            "active_facts": active,
            "superseded_facts": superseded,
            "total_facts": active + superseded,
            "by_class": by_class,
            "lessons": lessons,
            "entities": entities,
            "rebound_active": self._rebound_active,
            "session_writes": self._session_write_count,
        }

    def export_json(self) -> Dict:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM facts")
        facts = [self._row_to_fact(r).to_dict() for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM lessons")
        lessons = [
            {"id": r[0], "action": r[1], "context": r[2],
             "outcome": r[3], "insight": r[4], "created_at": r[5],
             "applied_count": r[6]}
            for r in cursor.fetchall()
        ]

        cursor.execute("SELECT * FROM entities")
        entities = [
            {"id": r[0], "name": r[1], "entity_type": r[2],
             "attributes": json.loads(r[3] or "{}"),
             "first_seen": r[4], "last_updated": r[5],
             "fact_ids": json.loads(r[6] or "[]")}
            for r in cursor.fetchall()
        ]

        if should_close:
            conn.close()

        return {
            "exported_at": self._now(),
            "facts": facts,
            "lessons": lessons,
            "entities": entities,
        }


def get_memory(db_path: str = None) -> AgentMemory:
    return AgentMemory(db_path)
