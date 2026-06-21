"""
AgentMemory - Persistent Memory for Hermes Agent

Based on Lena OpenClaw's agent-memory-1-0-0,
extended with Authority Lanes and Rebound-Protection
from the Moltbook signalfoundry/lucy17 discussions.

MIT License
"""

import sqlite3
import json
import hashlib
import re
import tempfile
import time
from types import MappingProxyType
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict

from text_norm import expand, query_terms


# ==================== AUTHORITY POLICY ====================

# Source trust order (most to least trusted):
# observation > conversation > inference > tool > external (untrusted)
KNOWN_SOURCES = ("observation", "conversation", "inference", "tool", "external")

_AUTHORITY_POLICY = {
    "identity": {
        "ttl_days": None,           # Never delete — Floor
        "half_life_days": None,     # No confidence decay — Identity is Floor
        "min_confidence": 0.9,
        "allowed_sources": ("observation", "conversation"),
        "single_valued": True,
    },
    "preference": {
        "ttl_days": 14,
        "half_life_days": 7,
        "min_confidence": 0.3,
        "allowed_sources": ("observation", "conversation"),
        "single_valued": False,
    },
    "evidence": {
        "ttl_days": 60,
        "half_life_days": 30,
        "min_confidence": 0.5,
        "allowed_sources": (
            "observation",
            "conversation",
            "inference",
            "tool",
            "external",
        ),
        "single_valued": False,
    },
    "authorization": {
        "ttl_days": 90,
        "half_life_days": 45,
        "min_confidence": 0.9,
        "allowed_sources": ("observation",),  # NOT from conversation
        "single_valued": True,
    },
    "procedural": {
        "ttl_days": 30,
        "half_life_days": None,     # rules don't decay; they expire or get re-approved
        "min_confidence": 0.5,
        "allowed_sources": ("observation",),  # observation-only, never conversation
        "single_valued": False,
    },
}
AUTHORITY_POLICY = MappingProxyType({
    key: MappingProxyType(value) for key, value in _AUTHORITY_POLICY.items()
})

# ==================== PROCEDURAL LANE (v3.6) ====================
# Behavioral rules are self-modifying behavior code: human review-gate,
# deterministic conflict detection, bounded injection, expiry.

# Max active rules per behavioral domain (drift containment).
PROCEDURAL_DOMAIN_BUDGET = {
    "language": 1,
    "response_style": 2,
    "code_policy": 2,
    "format_structure": 2,
    "initiative": 1,
}
PROCEDURAL_DEFAULT_DOMAIN_BUDGET = 1

# Hard cap on total active rules; above this, interactions are too hard to reason about.
PROCEDURAL_GLOBAL_CAP = 8

# Max cumulative artifact_cost across rules sharing a trigger context.
# This is the dominant drift class ("artifact bloat") per the swarm RFC data.
PROCEDURAL_ARTIFACT_BUDGET = 4

# Opposing effect values per dimension -> direct contradiction (blocks activation).
# 'language' is handled separately (any differing value contradicts).
PROCEDURAL_OPPOSITES = {
    "length": {
        "very_short": ("detailed",),
        "short": ("detailed",),
        "detailed": ("very_short", "short"),
    },
    "code": {
        "include": ("omit",),
        "omit": ("include",),
    },
    "initiative": {
        "reactive": ("proactive",),
        "proactive": ("reactive", "ask_first"),
        "ask_first": ("proactive",),
    },
    "tone": {
        "formal": ("casual",),
        "casual": ("formal",),
        "direct": ("explanatory",),
        "explanatory": ("direct",),
    },
}

PROCEDURAL_RULE_STATES = (
    "pending", "approved", "rejected", "retired", "superseded", "expired"
)

REBOUND_IDLE_THRESHOLD_HOURS = 6
REBOUND_MAX_FACTS_AFTER_IDLE = 3

ANOMALY_WRITES_PER_MINUTE = 20
ANOMALY_WINDOW_SECONDS = 60

LESSON_TTL_DAYS = 180
ENTITY_TTL_DAYS = 365
RECALL_TTL_DAYS = 30
AUDIT_RETENTION_DAYS = 90
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


@dataclass
class Snippet:
    id: str
    content: str
    source: str
    session_id: Optional[str]
    created_at: str
    expires_at: Optional[str]
    metadata: Dict[str, Any]


@dataclass
class ProceduralRule:
    id: str
    status: str
    domain: str
    trigger: Dict[str, Any]
    effect: Dict[str, Any]
    behavior_text: str
    priority: int
    tags: List[str]
    source: str
    confidence: float
    artifact_cost: int
    evidence_fact_ids: List[str]
    rationale: Optional[str]
    created_at: str
    approved_at: Optional[str] = None
    expires_at: Optional[str] = None
    review_due_at: Optional[str] = None
    last_matched_at: Optional[str] = None
    match_count: int = 0
    superseded_by: Optional[str] = None
    previous_rule_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class AgentMemory:
    """
    Persistent memory system for Hermes Agent.

    Based on Lena's agent-memory-1-0-0 with:
    - Authority Lanes (identity/preference/evidence/authorization)
    - Rebound-Protection after idle phases
    - Class-specific forget_stale()
    - Source-trust hierarchy

    Usage:
        mem = AgentMemory()
        mem.remember("Perry is the username", authority_class="identity",
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

        # Shared connection for :memory: (Tests) — SQLite in-memory
        # loses data when connection is closed
        self._shared_conn = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._snapshot_dir = Path(tempfile.mkdtemp(prefix="agent-memory-snap-"))
        else:
            self._snapshot_dir = Path(db_path).parent / "snapshots"
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self._synonyms = self._load_synonyms()
        self._check_rebound()

    def _connect(self):
        """Returns (conn, should_close)."""
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

        # Migration: add authority_class if not present
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
            CREATE TABLE IF NOT EXISTS fact_conflicts (
                id TEXT PRIMARY KEY,
                lane TEXT NOT NULL,
                tags TEXT,
                fact_a TEXT NOT NULL,
                fact_b TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entity_relations (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                to_id TEXT NOT NULL,
                attributes TEXT,
                created_at TEXT NOT NULL,
                last_accessed TEXT,
                expires_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS procedural_rules (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                domain TEXT NOT NULL,
                trigger_json TEXT NOT NULL,
                effect_json TEXT NOT NULL,
                behavior_text TEXT NOT NULL,
                priority INTEGER DEFAULT 50,
                tags TEXT,
                source TEXT NOT NULL DEFAULT 'observation',
                confidence REAL DEFAULT 0.5,
                artifact_cost INTEGER DEFAULT 0,
                evidence_fact_ids TEXT,
                rationale TEXT,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                expires_at TEXT,
                review_due_at TEXT,
                rejected_at TEXT,
                rejection_reason TEXT,
                retired_at TEXT,
                superseded_by TEXT,
                previous_rule_id TEXT,
                last_matched_at TEXT,
                match_count INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rule_conflicts (
                id TEXT PRIMARY KEY,
                rule_a TEXT NOT NULL,
                rule_b TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                dimension TEXT,
                reason TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT,
                resolution TEXT
            )
        """)

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recall_snippets (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT DEFAULT 'conversation',
                session_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                metadata TEXT
            )
        """)

        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS recall_snippets_fts
            USING fts5(content, tokenize='porter')
        """)

        self._migrate_session_log(cursor)
        self._cleanup_fts_orphans(cursor)
        self._cleanup_snippet_fts_orphans(cursor)
        self._create_fts_triggers(cursor)
        self._create_snippet_fts_triggers(cursor)
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
            CREATE INDEX IF NOT EXISTS idx_fact_conflicts_resolved
                ON fact_conflicts(resolved);
            CREATE INDEX IF NOT EXISTS idx_entity_relations_from
                ON entity_relations(from_id);
            CREATE INDEX IF NOT EXISTS idx_entity_relations_to
                ON entity_relations(to_id);
            CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON memory_audit(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_op
                ON memory_audit(op);
            CREATE INDEX IF NOT EXISTS idx_recall_snippets_session_time
                ON recall_snippets(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_recall_snippets_expires
                ON recall_snippets(expires_at);
            CREATE INDEX IF NOT EXISTS idx_procedural_status
                ON procedural_rules(status);
            CREATE INDEX IF NOT EXISTS idx_procedural_domain
                ON procedural_rules(domain);
            CREATE INDEX IF NOT EXISTS idx_procedural_expires
                ON procedural_rules(expires_at);
            CREATE INDEX IF NOT EXISTS idx_rule_conflicts_resolved
                ON rule_conflicts(resolved);
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

    def _cleanup_snippet_fts_orphans(self, cursor):
        cursor.execute("""
            DELETE FROM recall_snippets_fts
            WHERE rowid NOT IN (SELECT rowid FROM recall_snippets)
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

    def _create_snippet_fts_triggers(self, cursor):
        cursor.executescript("""
            CREATE TRIGGER IF NOT EXISTS recall_snippets_ai
            AFTER INSERT ON recall_snippets BEGIN
                INSERT INTO recall_snippets_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS recall_snippets_ad
            AFTER DELETE ON recall_snippets BEGIN
                DELETE FROM recall_snippets_fts WHERE rowid = old.rowid;
            END;

            CREATE TRIGGER IF NOT EXISTS recall_snippets_au
            AFTER UPDATE ON recall_snippets BEGIN
                DELETE FROM recall_snippets_fts WHERE rowid = old.rowid;
                INSERT INTO recall_snippets_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END;
        """)

    def _check_rebound(self):
        """
        Checks if Rebound mode is active.
        After >REBOUND_IDLE_THRESHOLD_HOURS without write: Rebound is active.
        identity class (Floor) never decreases.
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

    def get_meta(self, key: str) -> Optional[str]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM memory_meta WHERE key = ?", (key,))
        row = cursor.fetchone()
        if should_close:
            conn.close()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO memory_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
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
        """Writes a row to memory_audit. Append-only by convention."""
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
        """Rolling 60s write counter; logs anomaly if threshold exceeded."""
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
        """Deterministic content-hash ID. Same text in same lane = same fact."""
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

    def _quote_fts_query(self, query: str) -> str:
        """Treat user input as a literal FTS phrase, not FTS query syntax."""
        return f'"{query.replace(chr(34), chr(34) * 2)}"'

    def _terms_fts_query(self, query: str) -> str:
        """Turn natural language into a safe OR query for FTS5."""
        terms = re.findall(r"\w+", query)
        if not terms:
            return self._quote_fts_query(query)
        return " OR ".join(self._quote_fts_query(term) for term in terms)

    def _load_synonyms(self) -> Dict[str, List[str]]:
        """Load recall synonym expansions from the module-local JSON file."""
        try:
            with (Path(__file__).parent / "synonyms.json").open(
                encoding="utf-8"
            ) as handle:
                data = json.load(handle)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            key: values
            for key, values in data.items()
            if isinstance(key, str)
            and isinstance(values, list)
            and all(isinstance(value, str) for value in values)
        }

    def _smart_fts_query(self, query: str) -> str:
        """Build a safe token-prefix FTS query with synonym expansion."""
        terms = query_terms(query)
        if not terms:
            return self._terms_fts_query(query)

        expanded = [
            term
            for term in expand(terms, self._synonyms)
            if re.fullmatch(r"\w+", term)
        ]
        if not expanded:
            return self._terms_fts_query(query)
        return " OR ".join(f"{term}*" for term in expanded)

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

    def _effective_confidence(self, stored_confidence: float,
                              last_accessed: str,
                              authority_class: str) -> float:
        policy = AUTHORITY_POLICY.get(authority_class, AUTHORITY_POLICY["evidence"])
        half_life_days = policy.get("half_life_days")
        if half_life_days is None:
            return stored_confidence

        age_days = max(
            0.0,
            (self._utc_now() - self._parse_time(last_accessed)).total_seconds() / 86400
        )
        return stored_confidence * (0.5 ** (age_days / half_life_days))

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
            # Rolling TTL: expires_at is reset to NOW + policy TTL on every read
            # access. Facts in active use never expire; facts that are never read
            # again expire naturally at their (last_accessed + TTL) date.
            # Consequence: all facts loaded in the same session share the same
            # expires_at — this is expected, not a bug.
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

    def _snippet_select_columns(self, alias: str = None) -> str:
        prefix = f"{alias}." if alias else ""
        return (
            f"{prefix}id, {prefix}content, {prefix}source, "
            f"{prefix}session_id, {prefix}created_at, "
            f"{prefix}expires_at, {prefix}metadata"
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

    def _row_to_snippet(self, row) -> Snippet:
        return Snippet(
            id=row[0],
            content=row[1],
            source=row[2],
            session_id=row[3],
            created_at=row[4],
            expires_at=row[5],
            metadata=json.loads(row[6] or "{}"),
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

    def _touch_relation_ids(self, conn, relation_ids: List[str]):
        if not relation_ids:
            return
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)
        conn.executemany("""
            UPDATE entity_relations
            SET last_accessed = ?,
                expires_at = ?
            WHERE id = ?
        """, [(now, expires_at, relation_id) for relation_id in relation_ids])

    def _get_entity_by_id(self, conn, entity_id: str) -> Optional[Entity]:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {self._entity_select_columns()} FROM entities WHERE id = ?",
            (entity_id,)
        )
        row = cursor.fetchone()
        return self._row_to_entity(row) if row else None

    def _entity_ids_for_name(self, conn, name: str) -> List[str]:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM entities WHERE name = ? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (name, self._now())
        )
        return [row[0] for row in cursor.fetchall()]

    def _resolve_or_create_entity(self, conn, name: str,
                                  entity_type: Optional[str]) -> str:
        cursor = conn.cursor()
        if entity_type:
            cursor.execute(
                "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type)
            )
        else:
            cursor.execute(
                "SELECT id FROM entities WHERE name = ? ORDER BY last_updated DESC",
                (name,)
            )

        row = cursor.fetchone()
        if row:
            self._touch_entity_ids(conn, [row[0]])
            return row[0]

        entity_type = entity_type or "entity"
        entity_id = self._generate_id(f"{entity_type}:{name}")
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)
        cursor.execute("""
            INSERT INTO entities (id, name, entity_type, attributes,
                                 first_seen, last_updated, last_accessed,
                                 expires_at, fact_ids)
            VALUES (?, ?, ?, '{}', ?, ?, ?, ?, '[]')
        """, (entity_id, name, entity_type, now, now, now, expires_at))
        return entity_id

    def _detect_conflicts(self, conn, fact_id: str,
                          authority_class: str, tags: List[str]):
        if not tags:
            return

        sorted_tags = sorted(set(tags))
        tag_set = set(sorted_tags)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, tags FROM facts
            WHERE authority_class = ?
            AND superseded_by IS NULL
            AND id != ?
            AND (expires_at IS NULL OR expires_at > ?)
        """, (authority_class, fact_id, self._now()))

        for other_id, stored_tags in cursor.fetchall():
            other_tags = set(json.loads(stored_tags or "[]"))
            if other_tags != tag_set:
                continue
            conflict_id = self._generate_id(
                "|".join(sorted([fact_id, other_id])),
                authority_class="conflict",
            )
            cursor.execute("""
                INSERT OR IGNORE INTO fact_conflicts (
                    id, lane, tags, fact_a, fact_b, detected_at, resolved
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (
                conflict_id, authority_class, json.dumps(sorted_tags),
                fact_id, other_id, self._now()
            ))
            self._audit(
                "conflict_detected",
                fact_id=fact_id,
                authority_class=authority_class,
                metadata={"other_id": other_id, "tags": sorted_tags},
                conn=conn,
            )

    def _reconcile_conflicts(self, conn) -> int:
        """Mark conflicts resolved when either referenced fact is no longer active."""
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE fact_conflicts SET resolved = 1
            WHERE resolved = 0 AND (
                fact_a NOT IN (SELECT id FROM facts WHERE superseded_by IS NULL)
                OR fact_b NOT IN (SELECT id FROM facts WHERE superseded_by IS NULL)
            )
            """
        )
        return cursor.rowcount

    # ==================== FACTS ====================

    def remember(self, content: str, tags: List[str] = None,
                 source: str = "conversation", confidence: float = 1.0,
                 authority_class: str = "evidence",
                 expires_in_days: int = None) -> Optional[str]:
        """
        Stores a fact.

        Rebound-Protection: After idle max REBOUND_MAX_FACTS_AFTER_IDLE facts.
        identity is Floor — always allowed, no cap.
        authorization only allowed from 'observation'.
        """
        if authority_class == "procedural":
            # Behavioral rules never live in the facts table; route to propose_rule().
            self._audit(
                "policy_reject",
                content=content,
                authority_class=authority_class,
                source=source,
                accepted=False,
                reason="use_procedural_lane",
            )
            return None

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

        # Rebound-Protection only for new facts (identity = Floor, always allowed)
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

        if policy.get("single_valued"):
            # Consolidation may still collapse same lane/tag groups later; conflicts
            # provide write-time visibility plus an explicit resolution path.
            self._detect_conflicts(conn, fact_id, authority_class, tags)

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
            params = [self._smart_fts_query(query), min_confidence, self._now()]

            if authority_class:
                sql += " AND f.authority_class = ?"
                params.append(authority_class)

            sql += " ORDER BY fts.rank"

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            facts = []

            for row in rows:
                fact = self._row_to_fact(row)
                if tags and not all(t in fact.tags for t in tags):
                    continue
                if self._effective_confidence(
                    fact.confidence,
                    fact.last_accessed,
                    fact.authority_class
                ) < min_confidence:
                    continue
                facts.append(fact)
                if len(facts) >= limit:
                    break
            self._touch(conn, [fact.id for fact in facts])

            conn.commit()
            return facts
        finally:
            if should_close:
                conn.close()
            self._record_recall((time.perf_counter() - started_at) * 1000)

    def get_conflicts(self, include_resolved: bool = False) -> List[Dict[str, Any]]:
        conn, should_close = self._connect()
        cursor = conn.cursor()

        sql = """
            SELECT id, lane, tags, fact_a, fact_b, detected_at, resolved
            FROM fact_conflicts
            WHERE 1=1
        """
        if not include_resolved:
            sql += " AND resolved = 0"
        sql += " ORDER BY detected_at DESC, id DESC"

        cursor.execute(sql)
        rows = cursor.fetchall()

        fact_ids = sorted({row[3] for row in rows} | {row[4] for row in rows})
        facts: Dict[str, Dict[str, Any]] = {}
        if fact_ids:
            placeholders = ",".join("?" for _ in fact_ids)
            cursor.execute(
                "SELECT id, content, authority_class FROM facts "
                f"WHERE id IN ({placeholders})",
                fact_ids,
            )
            facts = {
                row[0]: {
                    "id": row[0],
                    "content": row[1],
                    "authority_class": row[2],
                }
                for row in cursor.fetchall()
            }

        if should_close:
            conn.close()

        def fact_ref(fact_id: str) -> Dict[str, Any]:
            return facts.get(fact_id, {
                "id": fact_id,
                "content": None,
                "authority_class": None,
            })

        return [
            {
                "id": row[0],
                "lane": row[1],
                "tags": json.loads(row[2] or "[]"),
                "fact_a": fact_ref(row[3]),
                "fact_b": fact_ref(row[4]),
                "detected_at": row[5],
                "resolved": bool(row[6]),
            }
            for row in rows
        ]

    def resolve_conflict(self, keep_id: str,
                         drop_ids: List[str]) -> Dict[str, Any]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        marked_resolved = 0
        dropped = []

        for drop_id in drop_ids:
            cursor.execute(
                "UPDATE facts SET superseded_by = ? WHERE id = ?",
                (keep_id, drop_id)
            )
            dropped.append(drop_id)
            drop_exists = cursor.rowcount > 0
            cursor.execute("""
                UPDATE fact_conflicts
                SET resolved = 1
                WHERE resolved = 0
                AND (
                    (fact_a = ? AND fact_b = ?)
                    OR (fact_a = ? AND fact_b = ?)
                )
            """, (keep_id, drop_id, drop_id, keep_id))
            marked_resolved += cursor.rowcount
            self._audit(
                "conflict_resolved",
                fact_id=keep_id,
                metadata={
                    "keep_id": keep_id,
                    "drop_id": drop_id,
                    "drop_exists": drop_exists,
                },
                conn=conn,
            )

        conn.commit()
        if should_close:
            conn.close()

        return {
            "kept": keep_id,
            "dropped": dropped,
            "marked_resolved": marked_resolved,
        }

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
            facts = []
            for row in rows:
                fact = self._row_to_fact(row)
                if self._effective_confidence(
                    fact.confidence,
                    fact.last_accessed,
                    fact.authority_class
                ) <= 0:
                    continue
                facts.append(fact)
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

    def consolidate(self, dry_run: bool = False) -> Dict[str, Any]:
        """Deterministic consolidation of active facts by lane+tag set."""
        facts = self.list_facts(limit=100000)
        groups: Dict[tuple, List[Fact]] = {}
        for fact in facts:
            key = (fact.authority_class, tuple(sorted(fact.tags)))
            groups.setdefault(key, []).append(fact)

        candidate_groups = [
            (key, group) for key, group in sorted(groups.items())
            if len(group) >= 2
        ]
        report = {
            "dry_run": dry_run,
            "groups_examined": len(candidate_groups),
            "facts_consolidated": 0,
            "facts_superseded": 0,
            "groups": [],
        }

        for key, group in candidate_groups:
            authority_class, tags = key
            ordered = sorted(
                group,
                key=lambda fact: (fact.confidence, fact.created_at, fact.id),
                reverse=True,
            )
            representative = ordered[0]
            old_ids = [fact.id for fact in ordered]
            consolidated_confidence = min(
                1.0,
                representative.confidence + 0.05 * (len(group) - 1)
            )
            group_report = {
                "authority_class": authority_class,
                "tags": list(tags),
                "representative_id": representative.id,
                "old_ids": old_ids,
                "new_id": representative.id if not dry_run else None,
                "confidence": consolidated_confidence,
                "superseded": len(group) - 1,
            }

            if not dry_run:
                conn, should_close = self._connect()
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        "UPDATE facts SET confidence = ?, tags = ? WHERE id = ?",
                        (consolidated_confidence, json.dumps(list(tags)), representative.id)
                    )
                    for old_id in old_ids:
                        if old_id == representative.id:
                            continue
                        cursor.execute(
                            "UPDATE facts SET superseded_by = ? WHERE id = ?",
                            (representative.id, old_id)
                        )
                        self._audit(
                            "supersede",
                            fact_id=representative.id,
                            metadata={
                                "old_id": old_id,
                                "new_id": representative.id,
                                "old_exists": cursor.rowcount > 0,
                                "reason": "consolidate",
                            },
                            conn=conn,
                        )
                    conn.commit()
                finally:
                    if should_close:
                        conn.close()

            report["facts_consolidated"] += 1
            report["facts_superseded"] += len(group) - 1
            report["groups"].append(group_report)

        if not dry_run:
            conn, should_close = self._connect()
            try:
                self._reconcile_conflicts(conn)
                conn.commit()
            finally:
                if should_close:
                    conn.close()

        return report

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
        if not old_exists:
            self._audit(
                "supersede_target_not_found",
                fact_id=old_fact_id,
                metadata={
                    "old_id": old_fact_id,
                    "new_id": new_id,
                    "reason": "old_fact_id did not match any active fact; "
                              "new fact was stored but old fact was not marked superseded",
                },
                conn=conn,
            )
        self._reconcile_conflicts(conn)
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
        self._reconcile_conflicts(conn)
        conn.commit()
        if should_close:
            conn.close()

    def forget_stale(self) -> Dict[str, int]:
        """
        Class-specific cleanup by policy TTL.
        identity is never deleted (Floor).
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

        self._reconcile_conflicts(conn)
        conn.commit()
        if should_close:
            conn.close()

        # procedural rules expire (status change), they are never row-deleted here
        deleted["procedural"] = self.expire_stale_rules()
        return deleted

    # ==================== RECALL SNIPPETS ====================

    def remember_snippet(self, content: str, source: str = "conversation",
                         session_id: Optional[str] = None,
                         metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Stores raw conversation recall separate from semantic facts.

        Snippets are not automatically injected; they are only retrievable
        via search_snippets().
        """
        metadata = metadata or {}
        snippet_id = self._generate_id(
            f"{session_id or ''}:{content}",
            authority_class="recall_snippet",
        )
        now = self._now()
        expires_at = self._expires_at_for_days(RECALL_TTL_DAYS)

        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO recall_snippets (
                id, content, source, session_id, created_at, expires_at, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            snippet_id,
            content,
            source,
            session_id,
            now,
            expires_at,
            json.dumps(metadata),
        ))
        self._audit(
            "snippet_write",
            fact_id=snippet_id,
            content=content,
            source=source,
            metadata={"session_id": session_id},
            conn=conn,
        )
        conn.commit()
        if should_close:
            conn.close()

        self._log_write()
        return snippet_id

    def search_snippets(self, query: str, limit: int = 10,
                        session_id: Optional[str] = None) -> List[Snippet]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        sql = f"""
            SELECT {self._snippet_select_columns("s")}
            FROM recall_snippets s
            JOIN recall_snippets_fts fts ON s.rowid = fts.rowid
            WHERE recall_snippets_fts MATCH ?
            AND (s.expires_at IS NULL OR s.expires_at > ?)
        """
        params: List[Any] = [self._quote_fts_query(query), self._now()]
        if session_id:
            sql += " AND s.session_id = ?"
            params.append(session_id)
        sql += " ORDER BY fts.rank LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        if should_close:
            conn.close()
        return [self._row_to_snippet(row) for row in rows]

    def forget_stale_snippets(self) -> int:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM recall_snippets
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
        """, (self._now(),))
        removed = cursor.rowcount
        if removed:
            self._audit(
                "forget_stale_snippets",
                metadata={"removed": removed},
                conn=conn,
            )
        conn.commit()
        if should_close:
            conn.close()
        return removed

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

    def relate(self, from_name: str, predicate: str, to_name: str,
               from_type: str = None, to_type: str = None,
               attributes: Dict[str, Any] = None) -> str:
        attributes = attributes or {}
        conn, should_close = self._connect()
        cursor = conn.cursor()

        from_id = self._resolve_or_create_entity(conn, from_name, from_type)
        to_id = self._resolve_or_create_entity(conn, to_name, to_type)
        relation_id = self._generate_id(
            f"{from_id}|{predicate}|{to_id}",
            authority_class="relation",
        )
        now = self._now()
        expires_at = self._expires_at_for_days(ENTITY_TTL_DAYS)

        cursor.execute(
            "SELECT attributes FROM entity_relations WHERE id = ?",
            (relation_id,)
        )
        row = cursor.fetchone()
        if row:
            merged_attributes = json.loads(row[0] or "{}")
            merged_attributes.update(attributes)
            cursor.execute("""
                UPDATE entity_relations
                SET attributes = ?, last_accessed = ?, expires_at = ?
                WHERE id = ?
            """, (json.dumps(merged_attributes), now, expires_at, relation_id))
        else:
            cursor.execute("""
                INSERT INTO entity_relations (
                    id, from_id, predicate, to_id, attributes,
                    created_at, last_accessed, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                relation_id, from_id, predicate, to_id, json.dumps(attributes),
                now, now, expires_at
            ))

        conn.commit()
        if should_close:
            conn.close()
        return relation_id

    def get_relations(self, name: str, direction: str = "both",
                      predicate: str = None) -> List[Dict[str, Any]]:
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be one of: out, in, both")

        conn, should_close = self._connect()
        cursor = conn.cursor()
        entity_ids = self._entity_ids_for_name(conn, name)
        if not entity_ids:
            if should_close:
                conn.close()
            return []

        placeholders = ",".join("?" for _ in entity_ids)
        params: List[Any] = []
        clauses = []
        if direction in ("out", "both"):
            clauses.append(f"r.from_id IN ({placeholders})")
            params.extend(entity_ids)
        if direction in ("in", "both"):
            clauses.append(f"r.to_id IN ({placeholders})")
            params.extend(entity_ids)

        sql = f"""
            SELECT r.id, r.from_id, r.predicate, r.to_id, r.attributes,
                   from_e.name, from_e.entity_type, to_e.name, to_e.entity_type
            FROM entity_relations r
            JOIN entities from_e ON from_e.id = r.from_id
            JOIN entities to_e ON to_e.id = r.to_id
            WHERE ({' OR '.join(clauses)})
            AND (r.expires_at IS NULL OR r.expires_at > ?)
        """
        params.append(self._now())
        if predicate:
            sql += " AND r.predicate = ?"
            params.append(predicate)
        sql += " ORDER BY r.last_accessed DESC, r.created_at DESC"

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        self._touch_relation_ids(conn, [row[0] for row in rows])
        self._touch_entity_ids(
            conn,
            sorted({row[1] for row in rows} | {row[3] for row in rows})
        )
        conn.commit()
        if should_close:
            conn.close()

        return [
            {
                "id": row[0],
                "from_id": row[1],
                "from_name": row[5],
                "from_type": row[6],
                "predicate": row[2],
                "to_id": row[3],
                "to_name": row[7],
                "to_type": row[8],
                "attributes": json.loads(row[4] or "{}"),
            }
            for row in rows
        ]

    def related_entities(self, name: str, predicate: str = None,
                         direction: str = "both") -> List[Entity]:
        relations = self.get_relations(
            name,
            direction=direction,
            predicate=predicate,
        )
        conn, should_close = self._connect()
        seen = set()
        entities = []
        try:
            for relation in relations:
                if relation["from_name"] == name:
                    neighbor_id = relation["to_id"]
                else:
                    neighbor_id = relation["from_id"]
                if neighbor_id in seen:
                    continue
                neighbor = self._get_entity_by_id(conn, neighbor_id)
                if neighbor:
                    seen.add(neighbor_id)
                    entities.append(neighbor)
        finally:
            if should_close:
                conn.close()
        return entities

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
        """Cleanup for expired lessons and entities."""
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

        cursor.execute("""
            DELETE FROM entity_relations
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
        """, (self._now(),))
        deleted["relations"] = cursor.rowcount
        if deleted["relations"]:
            self._audit(
                "forget_stale_lifecycle",
                metadata={"type": "relations", "removed": deleted["relations"]},
                conn=conn,
            )

        deleted["orphan_relations"] = self._prune_orphan_relations(conn=conn)

        conn.commit()
        if should_close:
            conn.close()
        return deleted

    def _prune_orphan_relations(self, conn=None) -> int:
        own_conn = conn is None
        if own_conn:
            conn, should_close = self._connect()
        else:
            should_close = False

        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM entity_relations
            WHERE from_id NOT IN (SELECT id FROM entities)
            OR to_id NOT IN (SELECT id FROM entities)
        """)
        removed = cursor.rowcount
        if removed:
            self._audit(
                "forget_stale_lifecycle",
                metadata={"type": "orphan_relations", "removed": removed},
                conn=conn,
            )
        if own_conn:
            conn.commit()
            if should_close:
                conn.close()
        return removed

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

        cursor.execute("SELECT COUNT(*) FROM entity_relations")
        relations = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM fact_conflicts WHERE resolved = 0")
        open_conflicts = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM procedural_rules WHERE status = 'pending'"
        )
        pending_rules = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM procedural_rules "
            "WHERE status = 'approved' AND (expires_at IS NULL OR expires_at > ?)",
            (self._now(),),
        )
        active_rules = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rule_conflicts WHERE resolved = 0")
        open_rule_conflicts = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM memory_audit")
        audit_rows = cursor.fetchone()[0]

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
            "relations": relations,
            "open_conflicts": open_conflicts,
            "pending_rules": pending_rules,
            "active_rules": active_rules,
            "open_rule_conflicts": open_rule_conflicts,
            "audit_rows": audit_rows,
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

    def _row_to_audit_entry(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "ts": row[1],
            "op": row[2],
            "fact_id": row[3],
            "content_hash": row[4],
            "authority_class": row[5],
            "source": row[6],
            "accepted": bool(row[7]),
            "reason": row[8],
            "metadata": json.loads(row[9]) if row[9] else None,
        }

    def get_audit(self, limit: int = 100, since: Optional[str] = None,
                  op: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns audit entries, newest first. since: ISO timestamp."""
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

        return [self._row_to_audit_entry(r) for r in rows]

    def get_provenance(self, fact_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Reconstruct a fact's audit chain (chronological). Read-only view over
        memory_audit; no separate provenance storage. Includes supersede events that
        reference this id in their metadata (old_id/new_id)."""
        conn, should_close = self._connect()
        cursor = conn.cursor()
        escaped_id = self._escape_like(fact_id)
        old_ref = f'%"old_id": "{escaped_id}"%'
        new_ref = f'%"new_id": "{escaped_id}"%'

        cursor.execute("""
            SELECT id, ts, op, fact_id, content_hash, authority_class,
                   source, accepted, reason, metadata
            FROM memory_audit
            WHERE fact_id = ?
            OR metadata LIKE ? ESCAPE '\\'
            OR metadata LIKE ? ESCAPE '\\'
            ORDER BY id ASC LIMIT ?
        """, (fact_id, old_ref, new_ref, limit))
        rows = cursor.fetchall()
        if should_close:
            conn.close()

        entries = []
        seen_ids = set()
        for row in rows:
            if row[0] in seen_ids:
                continue
            seen_ids.add(row[0])
            entries.append(self._row_to_audit_entry(row))
        return entries

    def anomalies(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.get_audit(limit=limit, op="anomaly_detected")

    def forget_old_audit(self, days: int = None) -> int:
        if days is None:
            days = AUDIT_RETENTION_DAYS

        cutoff = (self._utc_now() - timedelta(days=days)).isoformat()
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memory_audit WHERE ts < ?", (cutoff,))
        removed = cursor.rowcount
        if removed > 0:
            self._audit(
                "audit_pruned",
                metadata={"removed": removed, "days": days},
                conn=conn,
            )
        conn.commit()
        if should_close:
            conn.close()
        return removed

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
        """Copies current DB via SQLite's backup API to dest_path."""
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
        """Creates a snapshot of the current DB. Returns the path."""
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
        """Restores DB from a snapshot. Creates auto-backup before restoring."""
        src = Path(snapshot_path)
        if not src.is_file():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

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

        cursor.execute(f"SELECT {self._snippet_select_columns()} FROM recall_snippets")
        snippets = [asdict(self._row_to_snippet(r)) for r in cursor.fetchall()]

        if should_close:
            conn.close()

        return {
            "exported_at": self._now(),
            "facts": facts,
            "lessons": lessons,
            "entities": entities,
            "recall_snippets": snippets,
        }


    # ==================== PROCEDURAL LANE (v3.6) ====================

    _PROC_COLUMNS = (
        "id, status, domain, trigger_json, effect_json, behavior_text, "
        "priority, tags, source, confidence, artifact_cost, evidence_fact_ids, "
        "rationale, created_at, approved_at, expires_at, review_due_at, "
        "last_matched_at, match_count, superseded_by, previous_rule_id"
    )

    def _row_to_rule(self, row) -> ProceduralRule:
        return ProceduralRule(
            id=row[0],
            status=row[1],
            domain=row[2],
            trigger=json.loads(row[3] or "{}"),
            effect=json.loads(row[4] or "{}"),
            behavior_text=row[5],
            priority=row[6],
            tags=json.loads(row[7] or "[]"),
            source=row[8],
            confidence=row[9],
            artifact_cost=row[10],
            evidence_fact_ids=json.loads(row[11] or "[]"),
            rationale=row[12],
            created_at=row[13],
            approved_at=row[14],
            expires_at=row[15],
            review_due_at=row[16],
            last_matched_at=row[17],
            match_count=row[18],
            superseded_by=row[19],
            previous_rule_id=row[20],
        )

    def _rule_id(self, domain: str, trigger_json: str,
                 effect_json: str, behavior_text: str) -> str:
        payload = f"{domain}|{trigger_json}|{effect_json}|{behavior_text.strip()}"
        return "proc:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def _derive_artifact_cost(self, effect: Dict[str, Any]) -> int:
        """Deterministic output-weight estimate. Explicit value wins."""
        if "artifact_cost" in effect:
            try:
                return max(0, int(effect["artifact_cost"]))
            except (TypeError, ValueError):
                return 0
        cost = 0
        if effect.get("code") == "include":
            cost += 2
        if effect.get("structure") in ("sections", "table"):
            cost += 1
        if effect.get("length") == "detailed":
            cost += 1
        return cost

    def propose_rule(self, domain: str, trigger: Dict[str, Any],
                     effect: Dict[str, Any], behavior_text: str, *,
                     source: str = "observation",
                     evidence_fact_ids: Optional[List[str]] = None,
                     rationale: Optional[str] = None,
                     priority: int = 50, confidence: float = 0.5,
                     tags: Optional[List[str]] = None,
                     previous_rule_id: Optional[str] = None) -> Optional[str]:
        """
        Proposes a behavioral rule (status='pending'). Never auto-activates.

        observation-only: the agent may surface rule candidates, but cannot write
        free-form self-modifying instructions into the prompt.
        """
        policy = AUTHORITY_POLICY["procedural"]
        if source not in policy["allowed_sources"]:
            self._audit(
                "rule_propose_rejected",
                content=behavior_text,
                authority_class="procedural",
                source=source,
                accepted=False,
                reason="source_not_allowed",
            )
            return None

        if confidence < policy["min_confidence"]:
            self._audit(
                "rule_propose_rejected",
                content=behavior_text,
                authority_class="procedural",
                source=source,
                accepted=False,
                reason="low_confidence",
                metadata={"confidence": confidence,
                          "min_confidence": policy["min_confidence"]},
            )
            return None

        trigger_json = json.dumps(trigger, sort_keys=True)
        effect_json = json.dumps(effect, sort_keys=True)
        rule_id = self._rule_id(domain, trigger_json, effect_json, behavior_text)
        artifact_cost = self._derive_artifact_cost(effect)
        now = self._now()

        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM procedural_rules WHERE id = ?", (rule_id,)
        )
        existing = cursor.fetchone()
        if existing and existing[0] in ("pending", "approved"):
            self._audit(
                "rule_propose_duplicate",
                fact_id=rule_id,
                authority_class="procedural",
                source=source,
                metadata={"status": existing[0]},
                conn=conn,
            )
            conn.commit()
            if should_close:
                conn.close()
            return rule_id

        cursor.execute("""
            INSERT OR REPLACE INTO procedural_rules (
                id, status, domain, trigger_json, effect_json, behavior_text,
                priority, tags, source, confidence, artifact_cost,
                evidence_fact_ids, rationale, created_at, previous_rule_id,
                match_count
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            rule_id, domain, trigger_json, effect_json, behavior_text,
            priority, json.dumps(sorted(set(tags or []))), source, confidence,
            artifact_cost, json.dumps(evidence_fact_ids or []), rationale,
            now, previous_rule_id,
        ))
        self._audit(
            "rule_proposed",
            fact_id=rule_id,
            content=behavior_text,
            authority_class="procedural",
            source=source,
            metadata={
                "domain": domain,
                "artifact_cost": artifact_cost,
                "evidence_fact_ids": evidence_fact_ids or [],
                "previous_rule_id": previous_rule_id,
            },
            conn=conn,
        )
        conn.commit()
        if should_close:
            conn.close()
        self._log_write()
        return rule_id

    def _active_approved_rules(self, cursor,
                               exclude_id: Optional[str] = None
                               ) -> List[ProceduralRule]:
        cursor.execute(f"""
            SELECT {self._PROC_COLUMNS} FROM procedural_rules
            WHERE status = 'approved'
            AND (expires_at IS NULL OR expires_at > ?)
        """, (self._now(),))
        rules = [self._row_to_rule(r) for r in cursor.fetchall()]
        if exclude_id:
            rules = [r for r in rules if r.id != exclude_id]
        return rules

    def _trigger_overlap(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Two triggers conflict only if they can fire together."""
        if a.get("scope") == "always" or b.get("scope") == "always":
            return True
        a_tasks = set(a.get("task_class") or [])
        b_tasks = set(b.get("task_class") or [])
        if a_tasks and b_tasks:
            if a_tasks & b_tasks:
                return True
            # explicit, disjoint task classes do not overlap
            return False
        a_kw = set(a.get("keywords") or [])
        b_kw = set(b.get("keywords") or [])
        if a_kw and b_kw and (a_kw & b_kw):
            return True
        # unknown / underspecified triggers overlap conservatively
        if not a_tasks and not b_tasks and not a_kw and not b_kw:
            return True
        return False

    def _effect_contradiction(self, a: Dict[str, Any],
                              b: Dict[str, Any]) -> Optional[str]:
        """Returns the dimension name on which a and b directly contradict."""
        for dim, opposites in PROCEDURAL_OPPOSITES.items():
            av, bv = a.get(dim), b.get(dim)
            if av is None or bv is None:
                continue
            if bv in opposites.get(av, ()):
                return dim
        # language: any differing concrete value contradicts
        al, bl = a.get("language"), b.get("language")
        neutral = (None, "neutral", "mirror_user", "domain_based")
        if al not in neutral and bl not in neutral and al != bl:
            return "language"
        return None

    def _detect_rule_conflicts(self, conn, rule: ProceduralRule,
                               others: List[ProceduralRule]
                               ) -> List[Dict[str, Any]]:
        """Deterministic, stdlib-only conflict detection. Records to rule_conflicts."""
        cursor = conn.cursor()
        found: List[Dict[str, Any]] = []

        def record(other_id: str, conflict_type: str, severity: str,
                   dimension: Optional[str], reason: str):
            conflict_id = "rc:" + hashlib.sha256(
                "|".join(sorted([rule.id, other_id]) + [conflict_type]).encode("utf-8")
            ).hexdigest()[:12]
            cursor.execute("""
                INSERT OR IGNORE INTO rule_conflicts (
                    id, rule_a, rule_b, conflict_type, severity,
                    dimension, reason, detected_at, resolved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                conflict_id, rule.id, other_id, conflict_type, severity,
                dimension, reason, self._now(),
            ))
            self._audit(
                "rule_conflict_detected",
                fact_id=rule.id,
                authority_class="procedural",
                metadata={
                    "other_id": other_id,
                    "conflict_type": conflict_type,
                    "dimension": dimension,
                },
                conn=conn,
            )
            found.append({
                "other_id": other_id,
                "conflict_type": conflict_type,
                "severity": severity,
                "dimension": dimension,
                "reason": reason,
            })

        overlapping = [o for o in others
                       if self._trigger_overlap(rule.trigger, o.trigger)]

        for other in overlapping:
            dim = self._effect_contradiction(rule.effect, other.effect)
            if dim is not None:
                record(other.id, "contradiction", "high", dim,
                       f"Opposite '{dim}' values with overlapping triggers")
            elif other.domain == rule.domain:
                record(other.id, "interaction", "warning", None,
                       f"Same domain '{rule.domain}' with overlapping triggers")

        # artifact bloat: cumulative output cost across overlapping rules
        cumulative = rule.artifact_cost + sum(o.artifact_cost for o in overlapping)
        if cumulative > PROCEDURAL_ARTIFACT_BUDGET and overlapping:
            heaviest = max(overlapping, key=lambda o: o.artifact_cost)
            record(heaviest.id, "artifact_bloat", "high", None,
                   f"Cumulative artifact_cost {cumulative} exceeds budget "
                   f"{PROCEDURAL_ARTIFACT_BUDGET}")

        return found

    def approve_rule(self, rule_id: str, *, approved_by: Optional[str] = None,
                     ack_interactions: bool = False) -> Dict[str, Any]:
        """
        Human review-gate. No auto-approve, regardless of confidence.

        Hard-blocks on direct contradiction (ack cannot override).
        Soft-blocks on interaction / artifact bloat / budget / cap unless
        ack_interactions=True.
        """
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {self._PROC_COLUMNS} FROM procedural_rules WHERE id = ?",
            (rule_id,),
        )
        row = cursor.fetchone()
        if not row:
            if should_close:
                conn.close()
            return {"approved": False, "reason": "not_found"}

        rule = self._row_to_rule(row)
        if rule.status != "pending":
            if should_close:
                conn.close()
            return {"approved": False, "reason": f"not_pending ({rule.status})"}

        active = self._active_approved_rules(cursor, exclude_id=rule.id)
        if rule.previous_rule_id:
            # a rule replacing its predecessor must not conflict with it
            active = [r for r in active if r.id != rule.previous_rule_id]
        conflicts = self._detect_rule_conflicts(conn, rule, active)

        contradictions = [c for c in conflicts if c["conflict_type"] == "contradiction"]
        if contradictions:
            self._audit(
                "rule_approve_rejected",
                fact_id=rule.id,
                authority_class="procedural",
                accepted=False,
                reason="contradiction",
                metadata={"conflicts": contradictions},
                conn=conn,
            )
            conn.commit()
            if should_close:
                conn.close()
            return {"approved": False, "reason": "contradiction",
                    "conflicts": contradictions}

        soft = [c for c in conflicts if c["conflict_type"] != "contradiction"]

        domain_count = sum(1 for r in active if r.domain == rule.domain)
        domain_budget = PROCEDURAL_DOMAIN_BUDGET.get(
            rule.domain, PROCEDURAL_DEFAULT_DOMAIN_BUDGET
        )
        budget_blocks = []
        if domain_count >= domain_budget:
            budget_blocks.append({
                "type": "domain_budget",
                "domain": rule.domain,
                "count": domain_count,
                "budget": domain_budget,
            })
        if len(active) >= PROCEDURAL_GLOBAL_CAP:
            budget_blocks.append({
                "type": "global_cap",
                "count": len(active),
                "cap": PROCEDURAL_GLOBAL_CAP,
            })

        if (soft or budget_blocks) and not ack_interactions:
            self._audit(
                "rule_approve_blocked",
                fact_id=rule.id,
                authority_class="procedural",
                accepted=False,
                reason="needs_ack",
                metadata={"interactions": soft, "budget": budget_blocks},
                conn=conn,
            )
            conn.commit()
            if should_close:
                conn.close()
            return {"approved": False, "reason": "needs_ack",
                    "interactions": soft, "budget": budget_blocks}

        policy = AUTHORITY_POLICY["procedural"]
        approved_at = self._now()
        expires_at = self._expires_at_for_policy(policy)
        review_due_at = self._expires_at_for_days(policy["ttl_days"] - 7)

        # Optional supersession of an explicit predecessor.
        if rule.previous_rule_id:
            cursor.execute(
                "UPDATE procedural_rules SET status = 'superseded', "
                "superseded_by = ?, retired_at = ? "
                "WHERE id = ? AND status = 'approved'",
                (rule.id, approved_at, rule.previous_rule_id),
            )
            if cursor.rowcount:
                self._audit(
                    "rule_superseded",
                    fact_id=rule.previous_rule_id,
                    authority_class="procedural",
                    metadata={"superseded_by": rule.id},
                    conn=conn,
                )

        cursor.execute("""
            UPDATE procedural_rules
            SET status = 'approved', approved_at = ?, expires_at = ?,
                review_due_at = ?
            WHERE id = ?
        """, (approved_at, expires_at, review_due_at, rule.id))
        self._audit(
            "rule_approved",
            fact_id=rule.id,
            authority_class="procedural",
            metadata={
                "approved_by": approved_by,
                "ack_interactions": bool(soft or budget_blocks) and ack_interactions,
                "interactions": soft,
                "budget": budget_blocks,
            },
            conn=conn,
        )
        conn.commit()
        if should_close:
            conn.close()
        return {"approved": True, "rule_id": rule.id,
                "interactions": soft, "budget": budget_blocks}

    def reject_rule(self, rule_id: str, reason: str) -> bool:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE procedural_rules SET status = 'rejected', "
            "rejected_at = ?, rejection_reason = ? "
            "WHERE id = ? AND status = 'pending'",
            (self._now(), reason, rule_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            self._audit(
                "rule_rejected",
                fact_id=rule_id,
                authority_class="procedural",
                metadata={"reason": reason},
                conn=conn,
            )
        conn.commit()
        if should_close:
            conn.close()
        return changed

    def retire_rule(self, rule_id: str) -> bool:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE procedural_rules SET status = 'retired', retired_at = ? "
            "WHERE id = ? AND status = 'approved'",
            (self._now(), rule_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            self._reconcile_rule_conflicts(conn)
            self._audit(
                "rule_retired",
                fact_id=rule_id,
                authority_class="procedural",
                conn=conn,
            )
        conn.commit()
        if should_close:
            conn.close()
        return changed

    def get_pending_rules(self) -> List[ProceduralRule]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT {self._PROC_COLUMNS} FROM procedural_rules
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        rules = [self._row_to_rule(r) for r in cursor.fetchall()]
        if should_close:
            conn.close()
        return rules

    def get_active_rules(self, domain: Optional[str] = None
                         ) -> List[ProceduralRule]:
        """Active (injectable) rules.

        Note on lifecycle semantics: there is no separate 'active' status.
        A rule is "active" iff status == 'approved' and it has not expired.
        This is a deliberate choice for a stateless, session-less core: the
        plugin injects approved rules every turn (query-aware), so approval is
        activation. The RFC's 'approved -> active at session boundary' step is
        intentionally collapsed; if a session concept is later added, introduce
        a distinct 'active' status here rather than overloading 'approved'.
        """
        conn, should_close = self._connect()
        cursor = conn.cursor()
        sql = (f"SELECT {self._PROC_COLUMNS} FROM procedural_rules "
               "WHERE status = 'approved' "
               "AND (expires_at IS NULL OR expires_at > ?)")
        params: List[Any] = [self._now()]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY priority DESC, approved_at DESC"
        cursor.execute(sql, params)
        rules = [self._row_to_rule(r) for r in cursor.fetchall()]
        if should_close:
            conn.close()
        return rules

    def _rule_matches_query(self, rule: ProceduralRule,
                            query: Optional[str]) -> bool:
        if rule.trigger.get("scope") == "always":
            return True
        if not query:
            return True
        lowered = query.lower()
        for kw in rule.trigger.get("keywords") or []:
            if kw.lower() in lowered:
                return True
        for tc in rule.trigger.get("task_class") or []:
            if tc.lower() in lowered:
                return True
        # conditional trigger with no matching signal -> do not fire
        if rule.trigger.get("keywords") or rule.trigger.get("task_class"):
            return False
        return True

    def get_active_rules_for_injection(self, query: Optional[str] = None,
                                       limit: int = 5) -> List[ProceduralRule]:
        """Query-aware selection of injectable rules. Updates match telemetry."""
        candidates = self.get_active_rules()
        matched = [r for r in candidates if self._rule_matches_query(r, query)]
        matched = matched[:limit]
        if matched:
            conn, should_close = self._connect()
            cursor = conn.cursor()
            now = self._now()
            for rule in matched:
                cursor.execute(
                    "UPDATE procedural_rules "
                    "SET last_matched_at = ?, match_count = match_count + 1 "
                    "WHERE id = ?",
                    (now, rule.id),
                )
            conn.commit()
            if should_close:
                conn.close()
        return matched

    def _reconcile_rule_conflicts(self, conn) -> int:
        """Resolve conflicts once either referenced rule is no longer active."""
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE rule_conflicts SET resolved = 1, resolved_at = ?,
                resolution = 'auto: rule no longer active'
            WHERE resolved = 0 AND (
                rule_a NOT IN (
                    SELECT id FROM procedural_rules WHERE status = 'approved'
                )
                OR rule_b NOT IN (
                    SELECT id FROM procedural_rules WHERE status = 'approved'
                )
            )
        """, (self._now(),))
        return cursor.rowcount

    def get_rule_conflicts(self, include_resolved: bool = False
                           ) -> List[Dict[str, Any]]:
        conn, should_close = self._connect()
        cursor = conn.cursor()
        sql = """
            SELECT id, rule_a, rule_b, conflict_type, severity, dimension,
                   reason, detected_at, resolved, resolved_at, resolution
            FROM rule_conflicts WHERE 1=1
        """
        if not include_resolved:
            sql += " AND resolved = 0"
        sql += " ORDER BY detected_at DESC, id DESC"
        cursor.execute(sql)
        rows = cursor.fetchall()
        if should_close:
            conn.close()
        return [
            {
                "id": r[0],
                "rule_a": r[1],
                "rule_b": r[2],
                "conflict_type": r[3],
                "severity": r[4],
                "dimension": r[5],
                "reason": r[6],
                "detected_at": r[7],
                "resolved": bool(r[8]),
                "resolved_at": r[9],
                "resolution": r[10],
            }
            for r in rows
        ]

    def expire_stale_rules(self) -> int:
        """Move approved rules past their TTL to 'expired'. Called by forget_stale."""
        conn, should_close = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM procedural_rules
            WHERE status = 'approved'
            AND expires_at IS NOT NULL AND expires_at < ?
        """, (self._now(),))
        expired_ids = [r[0] for r in cursor.fetchall()]
        for rule_id in expired_ids:
            cursor.execute(
                "UPDATE procedural_rules SET status = 'expired' WHERE id = ?",
                (rule_id,),
            )
            self._audit(
                "rule_expired",
                fact_id=rule_id,
                authority_class="procedural",
                conn=conn,
            )
        if expired_ids:
            self._reconcile_rule_conflicts(conn)
        conn.commit()
        if should_close:
            conn.close()
        return len(expired_ids)


def get_memory(db_path: str = None) -> AgentMemory:
    return AgentMemory(db_path)
