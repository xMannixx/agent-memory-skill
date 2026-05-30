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
import tempfile
import time
from types import MappingProxyType
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict


# ==================== AUTHORITY POLICY ====================

_AUTHORITY_POLICY = {
    "identity": {
        "ttl_days": None,           # Nie löschen — Floor
        "min_confidence": 0.9,
        "allowed_sources": ("observation", "conversation"),
    },
    "preference": {
        "ttl_days": 14,
        "min_confidence": 0.3,
        "allowed_sources": ("conversation", "observation"),
    },
    "evidence": {
        "ttl_days": 60,
        "min_confidence": 0.5,
        "allowed_sources": ("conversation", "observation", "inference"),
    },
    "authorization": {
        "ttl_days": 90,
        "min_confidence": 0.9,
        "allowed_sources": ("observation",),  # NICHT aus conversation
    },
}
AUTHORITY_POLICY = MappingProxyType({
    key: MappingProxyType(value) for key, value in _AUTHORITY_POLICY.items()
})

REBOUND_IDLE_THRESHOLD_HOURS = 6
REBOUND_MAX_FACTS_AFTER_IDLE = 3

ANOMALY_WRITES_PER_MINUTE = 20
ANOMALY_WINDOW_SECONDS = 60

LESSON_TTL_DAYS = 180
ENTITY_TTL_DAYS = 365
STATS_LATENCY_WINDOW = 200


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
    last_accessed: str
    expires_at: Optional[str]
    applied_count: int = 0


@dataclass
class Entity:
    id: str
    name: str
    entity_type: str
    attributes: Dict[str, Any]
    first_seen: str
    last_updated: str
    last_accessed: str
    expires_at: Optional[str]
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
        self._recall_count = 0
        self._recall_latency_ms: List[float] = []
        self._rebound_write_count = 0
        self._session_start = self._utc_now()
        self._rebound_active = False
        self._write_timestamps: List[datetime] = []

        # Shared connection für :memory: (Tests) — SQLite in-memory
        # verliert Daten wenn Connection geschlossen wird
        self._shared_conn = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._snapshot_dir = Path(tempfile.mkdtemp(prefix="agent-memory-snap-"))
        else:
            self._snapshot_dir = Path(db_path).parent / "snapshots"
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)

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
                last_accessed TEXT,
                expires_at TEXT,
                applied_count INTEGER DEFAULT 0
            )
        """)

        self._migrate_lessons_lifecycle(cursor)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                attributes TEXT,
                first_seen TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                last_accessed TEXT,
                expires_at TEXT,
                fact_ids TEXT
            )
        """)

        self._migrate_entities_lifecycle(cursor)

        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(content, tags, tokenize='porter')
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                op TEXT NOT NULL,
                fact_id TEXT,
                content_hash TEXT,
                authority_class TEXT,
                source TEXT,
                accepted INTEGER NOT NULL DEFAULT 1,
                reason TEXT,
                metadata TEXT
            )
        """)

        self._migrate_session_log(cursor)
        self._cleanup_fts_orphans(cursor)
        self._create_fts_triggers(cursor)
        self._create_indexes(cursor)
        self._enable_wal_if_file_db(cursor)

        conn.commit()
        if should_close:
            conn.close()

    def _create_indexes(self, cursor):
        cursor.executescript("""
            CREATE INDEX IF NOT EXISTS idx_facts_class_super
                ON facts(authority_class, superseded_by);
            CREATE INDEX IF NOT EXISTS idx_facts_expires
                ON facts(expires_at);
            CREATE INDEX IF NOT EXISTS idx_facts_last_accessed
                ON facts(last_accessed);
            CREATE INDEX IF NOT EXISTS idx_lessons_outcome_time
                ON lessons(outcome, created_at);
            CREATE INDEX IF NOT EXISTS idx_entities_type_name
                ON entities(entity_type, name);
            CREATE INDEX IF NOT EXISTS idx_lessons_expires
                ON lessons(expires_at);
            CREATE INDEX IF NOT EXISTS idx_entities_expires
                ON entities(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON memory_audit(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_op
                ON memory_audit(op);
        """)

    def _enable_wal_if_file_db(self, cursor):
        if self.db_path == ":memory:":
            return
        cursor.execute("PRAGMA journal_mode=WAL")

    def _migrate_lessons_lifecycle(self, cursor):
        cursor.execute("PRAGMA table_info(lessons)")
        columns = [row[1] for row in cursor.fetchall()]
        if "last_accessed" not in columns:
            cursor.execute("ALTER TABLE lessons ADD COLUMN last_accessed TEXT")
        if "expires_at" not in columns:
            cursor.execute("ALTER TABLE lessons ADD COLUMN expires_at TEXT")

        expires_at = (
            f"datetime(created_at, '+{LESSON_TTL_DAYS} days')"
        )
        cursor.execute("""
            UPDATE lessons
            SET last_accessed = COALESCE(last_accessed, created_at),
                expires_at = COALESCE(expires_at, {})
            WHERE last_accessed IS NULL OR expires_at IS NULL
        """.format(expires_at))

    def _migrate_entities_lifecycle(self, cursor):
        cursor.execute("PRAGMA table_info(entities)")
        columns = [row[1] for row in cursor.fetchall()]
        if "last_accessed" not in columns:
            cursor.execute("ALTER TABLE entities ADD COLUMN last_accessed TEXT")
        if "expires_at" not in columns:
            cursor.execute("ALTER TABLE entities ADD COLUMN expires_at TEXT")

        expires_at = (
            f"datetime(last_updated, '+{ENTITY_TTL_DAYS} days')"
        )
        cursor.execute("""
            UPDATE entities
            SET last_accessed = COALESCE(last_accessed, last_updated),
                expires_at = COALESCE(expires_at, {})
            WHERE last_accessed IS NULL OR expires_at IS NULL
        """.format(expires_at))

    def _migrate_session_log(self, cursor):
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'session_log'
        """)
        if not cursor.fetchone():
            return

        cursor.execute(
            "SELECT last_write FROM session_log WHERE last_write IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row and row[0]:
            cursor.execute("""
                INSERT OR REPLACE INTO memory_meta (key, value)
                VALUES ('last_write', ?)
            """, (row[0],))
        cursor.execute("DROP TABLE session_log")

    def _cleanup_fts_orphans(self, cursor):
        cursor.execute("""
            DELETE FROM facts_fts
            WHERE rowid NOT IN (SELECT rowid FROM facts)
        """)

    def _create_fts_triggers(self, cursor):
        cursor.executescript("""
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                DELETE FROM facts_fts WHERE rowid = old.rowid;
            END;

            CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                DELETE FROM facts_fts WHERE rowid = old.rowid;
                INSERT INTO facts_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END;
        """)

    def _check_rebound(self):
        """
        Prüft ob Rebound-Modus aktiv.
        Nach >REBOUND_IDLE_THRESHOLD_HOURS ohne Write: Rebound aktiv.
        identity-Klasse (Floor) sinkt NIE ab.
        """
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM memory_meta WHERE key = 'last_write'"
        )
        row = cursor.fetchone()
        if should_close:
            conn.close()

        if row and row[0]:
            last_write = self._parse_time(row[0])
            idle_hours = (self._utc_now() - last_write).total_seconds() / 3600
            if idle_hours > REBOUND_IDLE_THRESHOLD_HOURS:
                self._rebound_active = True

    def _log_write(self):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO memory_meta (key, value)
            VALUES ('last_write', ?)
        """, (self._now(),))
        conn.commit()
        if should_close:
            conn.close()

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _audit(self, op: str, *, fact_id: Optional[str] = None,
               content: Optional[str] = None,
               authority_class: Optional[str] = None,
               source: Optional[str] = None,
               accepted: bool = True,
               reason: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None,
               conn: Optional[sqlite3.Connection] = None):
        """Schreibt eine Zeile in memory_audit. Append-only by convention."""
        own_conn = conn is None
        if own_conn:
            conn, should_close = self._connect()
        else:
            should_close = False

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memory_audit
                (ts, op, fact_id, content_hash, authority_class,
                 source, accepted, reason, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self._now(),
            op,
            fact_id,
            self._content_hash(content) if content is not None else None,
            authority_class,
            source,
            1 if accepted else 0,
            reason,
            json.dumps(metadata) if metadata else None,
        ))

        if own_conn:
            conn.commit()
            if should_close:
                conn.close()

    def _record_write_and_check_anomaly(self):
        """Rolling 60s Write-Counter; loggt Anomalie wenn Schwelle ueberschritten."""
        now = self._utc_now()
        cutoff = now - timedelta(seconds=ANOMALY_WINDOW_SECONDS)
        self._write_timestamps = [
            ts for ts in self._write_timestamps if ts >= cutoff
        ]
        self._write_timestamps.append(now)
        count = len(self._write_timestamps)
        if count > ANOMALY_WRITES_PER_MINUTE:
            self._audit(
                "anomaly_detected",
                reason="writes_per_minute_exceeded",
                metadata={
                    "count": count,
                    "window_seconds": ANOMALY_WINDOW_SECONDS,
                    "threshold": ANOMALY_WRITES_PER_MINUTE,
                },
            )

    def _record_recall(self, elapsed_ms: float):
        self._recall_count += 1
        self._recall_latency_ms.append(elapsed_ms)
        if len(self._recall_latency_ms) > STATS_LATENCY_WINDOW:
            self._recall_latency_ms = self._recall_latency_ms[-STATS_LATENCY_WINDOW:]

    def _recall_latency_stats(self) -> Dict[str, Any]:
        samples = sorted(self._recall_latency_ms)
        count = len(samples)
        if not samples:
            return {
                "count": 0,
                "avg": None,
                "p50": None,
                "p95": None,
                "max": None,
            }

        def percentile(value: float) -> float:
            index = min(count - 1, int((count - 1) * value))
            return samples[index]

        return {
            "count": count,
            "avg": sum(samples) / count,
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "max": samples[-1],
        }

    def _generate_id(self, content: str, authority_class: str = "evidence") -> str:
        """Deterministische Content-Hash-ID. Gleicher Text in gleicher Lane = gleicher Fakt."""
        normalized = content.strip()
        payload = f"{authority_class}:{normalized}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]

    def _escape_like(self, value: str) -> str:
        return (
            value
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now(self) -> str:
        return self._utc_now().isoformat()

    def _parse_time(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _expires_at_for_policy(self, policy: Dict[str, Any]) -> Optional[str]:
        if policy["ttl_days"] is None:
            return None
        return (self._utc_now() + timedelta(days=policy["ttl_days"])).isoformat()

    def _expires_at_for_days(self, days: int) -> str:
        return (self._utc_now() + timedelta(days=days)).isoformat()

    def _touch(self, conn, fact_ids: List[str]):
        if not fact_ids:
            return

        cursor = conn.cursor()
        now = self._now()
        for fact_id in fact_ids:
            cursor.execute(
                "SELECT authority_class FROM facts WHERE id = ?",
                (fact_id,)
            )
            row = cursor.fetchone()
            if not row:
                continue

            policy = AUTHORITY_POLICY.get(row[0], AUTHORITY_POLICY["evidence"])
            expires_at = self._expires_at_for_policy(policy)
            cursor.execute("""
                UPDATE facts
                SET last_accessed = ?,
                    expires_at = ?,
                    access_count = access_count + 1
                WHERE id = ?
            """, (now, expires_at, fact_id))


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

    def _fact_select_columns(self, alias: str = None) -> str:
        prefix = f"{alias}." if alias else ""
        return (
            f"{prefix}id, {prefix}content, {prefix}tags, {prefix}source, "
            f"{prefix}confidence, {prefix}authority_class, {prefix}created_at, "
            f"{prefix}last_accessed, {prefix}access_count, {prefix}expires_at, "
            f"{prefix}superseded_by"
        )

    def _lesson_select_columns(self) -> str:
        return (
            "id, action, context, outcome, insight, created_at, "
            "last_accessed, expires_at, applied_count"
        )

    def _entity_select_columns(self) -> str:
        return (
            "id, name, entity_type, attributes, first_seen, last_updated, "
            "last_accessed, expires_at, fact_ids"
        )

    def _row_to_lesson(self, row) -> Lesson:
        return Lesson(
            id=row[0], action=row[1], context=row[2],
            outcome=row[3], insight=row[4], created_at=row[5],
            last_accessed=row[6], expires_at=row[7],
            applied_count=row[8]
        )

    def _row_to_entity(self, row) -> Entity:
        return Entity(
            id=row[0], name=row[1], entity_type=row[2],
            attributes=json.loads(row[3] or "{}"),
            first_seen=row[4], last_updated=row[5],
            last_accessed=row[6], expires_at=row[7],
            fact_ids=json.loads(row[8] or "[]")
        )

    def _touch_lesson_ids(self, conn, lesson_ids: List[str]):
        if not lesson_ids:
            return
        now = self._now()
        expires_at = self._expires_at_for_days(LESSON_TTL_DAYS)
        conn.executemany("""
            UPDATE lessons
            SET last_accessed = ?,
                expires_at = ?
            WHERE id = ?
        """, [(now, expires_at, lesson_id) for lesson_id in lesson_ids])

    def _touch_entity_ids(self, conn, entity_ids: List[str]):
        if not entity_ids:
            return
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)
        conn.executemany("""
            UPDATE entities
            SET last_accessed = ?,
                expires_at = ?
            WHERE id = ?
        """, [(now, expires_at, entity_id) for entity_id in entity_ids])

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
            self._audit(
                "policy_reject",
                content=content,
                authority_class=authority_class,
                source=source,
                accepted=False,
                reason="source_not_allowed",
            )
            return None

        # Confidence-Filter
        if confidence < policy["min_confidence"]:
            self._audit(
                "policy_reject",
                content=content,
                authority_class=authority_class,
                source=source,
                accepted=False,
                reason="low_confidence",
                metadata={"confidence": confidence,
                          "min_confidence": policy["min_confidence"]},
            )
            return None

        fact_id = self._generate_id(content, authority_class)

        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,))
        exists = cursor.fetchone() is not None

        if exists:
            self._touch(conn, [fact_id])
            self._audit(
                "update",
                fact_id=fact_id,
                content=content,
                authority_class=authority_class,
                source=source,
                conn=conn,
            )
            conn.commit()
            if should_close:
                conn.close()
            self._session_write_count += 1
            self._log_write()
            return fact_id

        # Rebound-Protection nur bei echten neuen Facts (identity = Floor, immer erlaubt)
        if self._rebound_active and authority_class != "identity":
            if self._rebound_write_count >= REBOUND_MAX_FACTS_AFTER_IDLE:
                self._audit(
                    "rebound_reject",
                    content=content,
                    authority_class=authority_class,
                    source=source,
                    accepted=False,
                    reason="rebound_cap_exceeded",
                    conn=conn,
                )
                conn.commit()
                if should_close:
                    conn.close()
                return None
            self._rebound_write_count += 1

        now = self._now()
        tags = tags or []

        if expires_in_days is None and policy["ttl_days"] is not None:
            expires_in_days = policy["ttl_days"]

        expires_at = None
        if expires_in_days:
            expires_at = (self._utc_now() + timedelta(days=expires_in_days)).isoformat()

        cursor.execute("""
            INSERT INTO facts (id, content, tags, source, confidence,
                             authority_class, created_at, last_accessed,
                             access_count, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (fact_id, content, json.dumps(tags), source, confidence,
              authority_class, now, now, expires_at))

        self._audit(
            "write",
            fact_id=fact_id,
            content=content,
            authority_class=authority_class,
            source=source,
            conn=conn,
        )

        conn.commit()
        if should_close:
            conn.close()

        self._session_write_count += 1
        self._log_write()
        self._record_write_and_check_anomaly()
        return fact_id

    def recall(self, query: str, limit: int = 10,
               tags: List[str] = None, min_confidence: float = 0.3,
               authority_class: str = None) -> List[Fact]:
        started_at = time.perf_counter()
        conn, should_close = self._connect()
        cursor = conn.cursor()
        try:
            sql = f"""
                SELECT {self._fact_select_columns("f")} FROM facts f
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
            self._touch(conn, [fact.id for fact in facts])

            conn.commit()
            return facts
        finally:
            if should_close:
                conn.close()
            self._record_recall((time.perf_counter() - started_at) * 1000)

    def recall_by_authority(self, authority_class: str, limit: int = 50) -> List[Fact]:
        started_at = time.perf_counter()
        conn, should_close = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                SELECT {self._fact_select_columns()} FROM facts
                WHERE authority_class = ?
                AND superseded_by IS NULL
                AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY last_accessed DESC LIMIT ?
            """, (authority_class, self._now(), limit))
            rows = cursor.fetchall()
            facts = [self._row_to_fact(r) for r in rows]
            self._touch(conn, [fact.id for fact in facts])
            conn.commit()
            return facts
        finally:
            if should_close:
                conn.close()
            self._record_recall((time.perf_counter() - started_at) * 1000)

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {self._fact_select_columns()} FROM facts WHERE id = ?",
            (fact_id,)
        )
        row = cursor.fetchone()
        if row:
            self._touch(conn, [fact_id])
            cursor.execute(
                f"SELECT {self._fact_select_columns()} FROM facts WHERE id = ?",
                (fact_id,)
            )
            row = cursor.fetchone()
            conn.commit()
        if should_close:
            conn.close()
        return self._row_to_fact(row) if row else None

    def list_facts(self, tags: List[str] = None, limit: int = 50,
                   authority_class: str = None,
                   include_superseded: bool = False) -> List[Fact]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = f"SELECT {self._fact_select_columns()} FROM facts WHERE 1=1"
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
        old_exists = cursor.rowcount > 0
        self._audit(
            "supersede",
            fact_id=new_id,
            metadata={
                "old_id": old_fact_id,
                "new_id": new_id,
                "old_exists": old_exists,
            },
            conn=conn,
        )
        conn.commit()
        if should_close:
            conn.close()
        return new_id

    def forget(self, fact_id: str):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._audit(
            "forget",
            fact_id=fact_id,
            metadata={"removed": cursor.rowcount},
            conn=conn,
        )
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

            cursor.execute("""
                DELETE FROM facts
                WHERE authority_class = ?
                AND expires_at IS NOT NULL
                AND expires_at < ?
                AND superseded_by IS NULL
            """, (cls, self._now()))
            removed = cursor.rowcount
            deleted[cls] = removed
            if removed:
                self._audit(
                    "forget_stale",
                    authority_class=cls,
                    metadata={"removed": removed},
                    conn=conn,
                )

        conn.commit()
        if should_close:
            conn.close()
        return deleted

    # ==================== LESSONS ====================

    def learn(self, action: str, context: str, outcome: str, insight: str) -> str:
        lesson_id = self._generate_id(f"{action}{context}")
        now = self._now()
        expires_at = self._expires_at_for_days(LESSON_TTL_DAYS)
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO lessons (
                id, action, context, outcome, insight, created_at,
                last_accessed, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lesson_id, action, context, outcome, insight,
            now, now, expires_at
        ))
        conn.commit()
        if should_close:
            conn.close()
        return lesson_id

    def get_lessons(self, context: str = None, outcome: str = None,
                    limit: int = 10) -> List[Lesson]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = f"SELECT {self._lesson_select_columns()} FROM lessons WHERE 1=1"
        params = []

        if context:
            sql += " AND context LIKE ? ESCAPE '\\'"
            params.append(f"%{self._escape_like(context)}%")
        if outcome:
            sql += " AND outcome = ?"
            params.append(outcome)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        self._touch_lesson_ids(conn, [row[0] for row in rows])
        conn.commit()
        if should_close:
            conn.close()

        return [self._row_to_lesson(r) for r in rows]

    def apply_lesson(self, lesson_id: str):
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lessons SET applied_count = applied_count + 1 WHERE id = ?",
            (lesson_id,)
        )
        self._touch_lesson_ids(conn, [lesson_id])
        conn.commit()
        if should_close:
            conn.close()

    # ==================== ENTITIES ====================

    def track_entity(self, name: str, entity_type: str,
                     attributes: Dict[str, Any] = None) -> str:
        entity_id = self._generate_id(f"{entity_type}:{name}")
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)
        attributes = attributes or {}

        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, attributes FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        )
        existing = cursor.fetchone()

        if existing:
            merged_attributes = json.loads(existing[1] or "{}")
            merged_attributes.update(attributes)
            cursor.execute(
                "UPDATE entities SET attributes = ?, last_updated = ?, "
                "last_accessed = ?, expires_at = ? WHERE id = ?",
                (json.dumps(merged_attributes), now, now, expires_at, existing[0])
            )
            entity_id = existing[0]
        else:
            cursor.execute("""
                INSERT INTO entities (id, name, entity_type, attributes,
                                     first_seen, last_updated, last_accessed,
                                     expires_at, fact_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')
            """, (
                entity_id, name, entity_type, json.dumps(attributes),
                now, now, now, expires_at
            ))

        conn.commit()
        if should_close:
            conn.close()
        return entity_id

    def get_entity(self, name: str, entity_type: str = None) -> Optional[Entity]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        if entity_type:
            cursor.execute(
                f"SELECT {self._entity_select_columns()} "
                "FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type)
            )
        else:
            cursor.execute(
                f"SELECT {self._entity_select_columns()} "
                "FROM entities WHERE name = ?",
                (name,)
            )

        row = cursor.fetchone()
        if row:
            self._touch_entity_ids(conn, [row[0]])
            cursor.execute(
                f"SELECT {self._entity_select_columns()} "
                "FROM entities WHERE id = ?",
                (row[0],)
            )
            row = cursor.fetchone()
            conn.commit()
        if should_close:
            conn.close()

        if not row:
            return None

        return self._row_to_entity(row)

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
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)
        cursor.execute(
            "UPDATE entities SET attributes = ?, last_updated = ?, "
            "last_accessed = ?, expires_at = ? WHERE id = ?",
            (json.dumps(existing), now, now, expires_at, row[0])
        )
        cursor.execute(
            f"SELECT {self._entity_select_columns()} FROM entities WHERE id = ?",
            (row[0],)
        )
        updated = cursor.fetchone()
        conn.commit()
        if should_close:
            conn.close()
        return self._row_to_entity(updated)

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
                    "UPDATE entities SET fact_ids = ?, last_accessed = ?, "
                    "expires_at = ? WHERE id = ?",
                    (
                        json.dumps(fact_ids),
                        self._now(),
                        self._expires_at_for_days(ENTITY_TTL_DAYS),
                        row[0],
                    )
                )
        conn.commit()
        if should_close:
            conn.close()

    def list_entities(self, entity_type: str = None) -> List[Entity]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        if entity_type:
            cursor.execute(
                f"SELECT {self._entity_select_columns()} FROM entities "
                "WHERE entity_type = ? ORDER BY last_updated DESC",
                (entity_type,)
            )
        else:
            cursor.execute(
                f"SELECT {self._entity_select_columns()} "
                "FROM entities ORDER BY last_updated DESC"
            )

        rows = cursor.fetchall()
        if should_close:
            conn.close()
        return [self._row_to_entity(r) for r in rows]

    def forget_stale_lifecycle(self) -> Dict[str, int]:
        """Cleanup fuer abgelaufene Lessons und Entities."""
        conn, should_close = self._connect()
        cursor = conn.cursor()
        deleted = {}

        cursor.execute("""
            DELETE FROM lessons
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
        """, (self._now(),))
        deleted["lessons"] = cursor.rowcount
        if deleted["lessons"]:
            self._audit(
                "forget_stale_lifecycle",
                metadata={"type": "lessons", "removed": deleted["lessons"]},
                conn=conn,
            )

        cursor.execute("""
            DELETE FROM entities
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
        """, (self._now(),))
        deleted["entities"] = cursor.rowcount
        if deleted["entities"]:
            self._audit(
                "forget_stale_lifecycle",
                metadata={"type": "entities", "removed": deleted["entities"]},
                conn=conn,
            )

        conn.commit()
        if should_close:
            conn.close()
        return deleted

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

        cursor.execute("""
            SELECT COUNT(*) FROM facts
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
            AND superseded_by IS NULL
        """, (self._now(),))
        stale_facts = cursor.fetchone()[0]

        if should_close:
            conn.close()

        total_facts = active + superseded
        by_class_ratio = {
            cls: count / active for cls, count in by_class.items()
        } if active else {}

        return {
            "active_facts": active,
            "superseded_facts": superseded,
            "total_facts": total_facts,
            "by_class": by_class,
            "by_class_ratio": by_class_ratio,
            "lessons": lessons,
            "entities": entities,
            "stale_facts": stale_facts,
            "stale_ratio": stale_facts / active if active else 0.0,
            "superseded_ratio": superseded / total_facts if total_facts else 0.0,
            "recalls": self._recall_count,
            "recall_latency_ms": self._recall_latency_stats(),
            "rebound_active": self._rebound_active,
            "session_writes": self._session_write_count,
            "rebound_remaining": max(
                0,
                REBOUND_MAX_FACTS_AFTER_IDLE - self._rebound_write_count
            ) if self._rebound_active else None,
        }

    # ==================== AUDIT / ANOMALY ====================

    def get_audit(self, limit: int = 100, since: Optional[str] = None,
                  op: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liefert Audit-Eintraege, neueste zuerst. since: ISO-Timestamp."""
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = "SELECT id, ts, op, fact_id, content_hash, authority_class, " \
              "source, accepted, reason, metadata FROM memory_audit WHERE 1=1"
        params: List[Any] = []
        if op:
            sql += " AND op = ?"
            params.append(op)
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        if should_close:
            conn.close()

        return [
            {
                "id": r[0],
                "ts": r[1],
                "op": r[2],
                "fact_id": r[3],
                "content_hash": r[4],
                "authority_class": r[5],
                "source": r[6],
                "accepted": bool(r[7]),
                "reason": r[8],
                "metadata": json.loads(r[9]) if r[9] else None,
            }
            for r in rows
        ]

    def anomalies(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.get_audit(limit=limit, op="anomaly_detected")

    # ==================== SNAPSHOTS ====================

    def _snapshot_filename(self, label: Optional[str] = None) -> str:
        ts = self._utc_now().strftime("%Y%m%dT%H%M%S")
        safe_label = ""
        if label:
            safe_label = "-" + "".join(
                c if c.isalnum() or c in ("-", "_") else "_" for c in label
            )
        return f"snapshot-{ts}{safe_label}.db"

    def _backup_to(self, dest_path: Path):
        """Kopiert die aktuelle DB ueber SQLite's Backup-API nach dest_path."""
        target = sqlite3.connect(str(dest_path))
        try:
            if self._shared_conn is not None:
                self._shared_conn.backup(target)
            else:
                source = sqlite3.connect(self.db_path)
                try:
                    source.backup(target)
                finally:
                    source.close()
        finally:
            target.close()

    def snapshot(self, label: Optional[str] = None) -> str:
        """Erstellt einen Snapshot der aktuellen DB. Gibt den Pfad zurueck."""
        path = self._snapshot_dir / self._snapshot_filename(label)
        self._backup_to(path)
        self._audit(
            "snapshot",
            metadata={"path": str(path), "label": label},
        )
        return str(path)

    def list_snapshots(self) -> List[Dict[str, Any]]:
        if not self._snapshot_dir.exists():
            return []
        entries = []
        for p in sorted(self._snapshot_dir.glob("*.db")):
            stat = p.stat()
            entries.append({
                "path": str(p),
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "size_bytes": stat.st_size,
                "label": p.stem,
            })
        return entries

    def restore(self, snapshot_path: str) -> None:
        """Restauriert die DB aus einem Snapshot. Erstellt vorher Auto-Backup."""
        src = Path(snapshot_path)
        if not src.is_file():
            raise FileNotFoundError(f"Snapshot nicht gefunden: {snapshot_path}")

        pre_path = self._snapshot_dir / self._snapshot_filename(
            label=f"pre-restore"
        )
        self._backup_to(pre_path)

        source = sqlite3.connect(str(src))
        try:
            if self._shared_conn is not None:
                source.backup(self._shared_conn)
            else:
                target = sqlite3.connect(self.db_path)
                try:
                    source.backup(target)
                finally:
                    target.close()
        finally:
            source.close()

        self._audit(
            "restore",
            metadata={
                "snapshot": str(src),
                "pre_restore_backup": str(pre_path),
            },
        )

    def export_json(self) -> Dict:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {self._fact_select_columns()} FROM facts")
        facts = [self._row_to_fact(r).to_dict() for r in cursor.fetchall()]

        cursor.execute(f"SELECT {self._lesson_select_columns()} FROM lessons")
        lessons = [
            {"id": r[0], "action": r[1], "context": r[2],
             "outcome": r[3], "insight": r[4], "created_at": r[5],
             "last_accessed": r[6], "expires_at": r[7],
             "applied_count": r[8]}
            for r in cursor.fetchall()
        ]

        cursor.execute(f"SELECT {self._entity_select_columns()} FROM entities")
        entities = [
            {"id": r[0], "name": r[1], "entity_type": r[2],
             "attributes": json.loads(r[3] or "{}"),
             "first_seen": r[4], "last_updated": r[5],
             "last_accessed": r[6], "expires_at": r[7],
             "fact_ids": json.loads(r[8] or "[]")}
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
