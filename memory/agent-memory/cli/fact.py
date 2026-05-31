#!/usr/bin/env python3
"""CLI wrapper for AgentMemory — Hermes Edition."""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from memory import AgentMemory, AUTHORITY_POLICY


def main():
    parser = argparse.ArgumentParser(description="Hermes Agent Memory CLI")
    parser.add_argument("--db", help="Database path", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    add_p = subparsers.add_parser("add", help="Save fact")
    add_p.add_argument("content", help="The fact")
    add_p.add_argument("--tags", "-t", nargs="+", default=[], help="Tags")
    add_p.add_argument("--source", "-s", default="conversation",
                       choices=["conversation", "observation", "inference"])
    add_p.add_argument("--confidence", "-c", type=float, default=0.9)
    add_p.add_argument("--authority", "-a", default="evidence",
                       choices=list(AUTHORITY_POLICY.keys()),
                       help="Authority class: identity/preference/evidence/authorization")
    add_p.add_argument("--expires", "-e", type=int, help="Expiry in days")

    # recall
    recall_p = subparsers.add_parser("recall", help="Search facts")
    recall_p.add_argument("query")
    recall_p.add_argument("--limit", "-n", type=int, default=10)
    recall_p.add_argument("--tags", "-t", nargs="+")
    recall_p.add_argument("--authority", "-a", default=None,
                          choices=list(AUTHORITY_POLICY.keys()))

    # list
    list_p = subparsers.add_parser("list", help="List all facts")
    list_p.add_argument("--tags", "-t", nargs="+")
    list_p.add_argument("--limit", "-n", type=int, default=20)
    list_p.add_argument("--authority", "-a", default=None,
                        choices=list(AUTHORITY_POLICY.keys()))

    # supersede
    sup_p = subparsers.add_parser("supersede", help="Replace fact")
    sup_p.add_argument("fact_id")
    sup_p.add_argument("new_content")
    sup_p.add_argument("--authority", "-a", default="evidence",
                       choices=list(AUTHORITY_POLICY.keys()))

    # forget
    subparsers.add_parser("forget-stale", help="Delete expired facts (Policy TTL)")

    # stats
    subparsers.add_parser("stats", help="Statistics")

    # learn
    learn_p = subparsers.add_parser("learn", help="Save lesson")
    learn_p.add_argument("action")
    learn_p.add_argument("context")
    learn_p.add_argument("outcome", choices=["positive", "negative", "neutral"])
    learn_p.add_argument("insight")

    # lessons
    lessons_p = subparsers.add_parser("lessons", help="Retrieve lessons")
    lessons_p.add_argument("--context", "-c")
    lessons_p.add_argument("--outcome", "-o",
                           choices=["positive", "negative", "neutral"])
    lessons_p.add_argument("--limit", "-n", type=int, default=10)

    # audit
    audit_p = subparsers.add_parser("audit", help="Show audit log")
    audit_p.add_argument("--limit", "-n", type=int, default=20)
    audit_p.add_argument("--op", help="Filter by operation type")

    # audit-prune
    audit_prune_p = subparsers.add_parser(
        "audit-prune",
        help="Prune old audit log rows"
    )
    audit_prune_p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention window in days"
    )

    # snapshot
    snap_p = subparsers.add_parser("snapshot", help="Create DB snapshot")
    snap_p.add_argument("--label", help="Optional label")

    # snapshots
    subparsers.add_parser("snapshots", help="List existing snapshots")

    # restore
    restore_p = subparsers.add_parser("restore", help="Restore DB from snapshot")
    restore_p.add_argument("path", help="Path to snapshot")

    # anomalies
    anom_p = subparsers.add_parser("anomalies", help="Show anomaly entries")
    anom_p.add_argument("--limit", "-n", type=int, default=10)

    # consolidate
    consolidate_p = subparsers.add_parser("consolidate", help="Consolidate similar facts")
    consolidate_p.add_argument("--dry-run", action="store_true",
                               help="Generate report only, no DB changes")

    # snippet
    snippet_p = subparsers.add_parser(
        "snippet",
        help="Save or search raw conversation snippets"
    )
    snippet_sub = snippet_p.add_subparsers(dest="snippet_command", required=True)

    snippet_add_p = snippet_sub.add_parser("add", help="Save snippet")
    snippet_add_p.add_argument("content", help="Raw snippet")
    snippet_add_p.add_argument("--source", "-s", default="conversation")
    snippet_add_p.add_argument("--session", help="Optional session ID")

    snippet_search_p = snippet_sub.add_parser("search", help="Search snippets")
    snippet_search_p.add_argument("query")
    snippet_search_p.add_argument("--limit", "-n", type=int, default=10)
    snippet_search_p.add_argument("--session", help="Optional session ID")

    # doctor
    subparsers.add_parser("doctor", help="Diagnose memory/plugin setup")

    args = parser.parse_args()
    mem = AgentMemory(db_path=args.db)

    if args.command == "add":
        fact_id = mem.remember(
            args.content,
            tags=args.tags,
            source=args.source,
            confidence=args.confidence,
            authority_class=args.authority,
            expires_in_days=args.expires
        )
        if fact_id:
            print(f"OK [{fact_id}] ({args.authority}): {args.content[:60]}")
        else:
            print(f"REJECTED — Authority policy or rebound protection took effect")

    elif args.command == "recall":
        facts = mem.recall(args.query, limit=args.limit, tags=args.tags,
                           authority_class=args.authority)
        if not facts:
            print("No matches.")
        for f in facts:
            tags = " ".join(f"#{t}" for t in f.tags) if f.tags else ""
            print(f"[{f.id}] ({f.authority_class}/{f.source} conf={f.confidence}) {f.content} {tags}")

    elif args.command == "list":
        facts = mem.list_facts(tags=args.tags, limit=args.limit,
                               authority_class=args.authority)
        for f in facts:
            tags = " ".join(f"#{t}" for t in f.tags) if f.tags else ""
            print(f"[{f.id}] ({f.authority_class}) {f.content[:70]} {tags}")

    elif args.command == "supersede":
        new_id = mem.supersede(args.fact_id, args.new_content,
                               authority_class=args.authority)
        if new_id:
            print(f"OK [{new_id}] replaces {args.fact_id}")
        else:
            print(f"ERROR — Replacement was rejected by policy")

    elif args.command == "forget-stale":
        result = mem.forget_stale()
        total = sum(result.values())
        print(f"Deleted: {total} facts")
        for cls, count in result.items():
            print(f"  {cls}: {count}")

    elif args.command == "stats":
        s = mem.stats()
        print(f"Active facts:     {s['active_facts']}")
        print(f"Superseded:       {s['superseded_facts']}")
        print(f"Lessons:          {s['lessons']}")
        print(f"Entities:         {s['entities']}")
        print(f"Audit rows:       {s['audit_rows']}")
        print(f"Rebound active:   {s['rebound_active']}")
        print(f"Rebound remaining: {s['rebound_remaining']}")
        print(f"Session writes:   {s['session_writes']}")
        print(f"Recalls:          {s['recalls']}")
        latency = s.get("recall_latency_ms", {})
        if latency.get("count"):
            print(
                "Recall latency:   "
                f"avg={latency['avg']:.2f}ms "
                f"p50={latency['p50']:.2f}ms "
                f"p95={latency['p95']:.2f}ms "
                f"max={latency['max']:.2f}ms"
            )
        else:
            print("Recall latency:   no data")
        print(
            f"Stale facts:      {s['stale_facts']} "
            f"({s['stale_ratio']:.1%})"
        )
        print(f"Superseded ratio: {s['superseded_ratio']:.1%}")
        print(f"By class:")
        for cls, count in s.get("by_class", {}).items():
            ratio = s.get("by_class_ratio", {}).get(cls, 0.0)
            print(f"  {cls}: {count} ({ratio:.1%})")

    elif args.command == "learn":
        lid = mem.learn(args.action, args.context, args.outcome, args.insight)
        print(f"OK [{lid}] Lesson saved")

    elif args.command == "lessons":
        lessons = mem.get_lessons(context=args.context, outcome=args.outcome,
                                  limit=args.limit)
        if not lessons:
            print("No lessons found.")
        for l in lessons:
            print(f"[{l.id}] [{l.outcome}] {l.action} → {l.insight}")

    elif args.command == "audit":
        entries = mem.get_audit(limit=args.limit, op=args.op)
        if not entries:
            print("No audit entries.")
        for e in entries:
            flag = "OK " if e["accepted"] else "REJ"
            reason = f" reason={e['reason']}" if e["reason"] else ""
            fid = f" fact={e['fact_id']}" if e["fact_id"] else ""
            cls = f" {e['authority_class']}" if e["authority_class"] else ""
            print(f"[{e['ts']}] {flag} {e['op']}{cls}{fid}{reason}")

    elif args.command == "audit-prune":
        removed = mem.forget_old_audit(days=args.days)
        print(f"Pruned {removed} audit rows")

    elif args.command == "snapshot":
        path = mem.snapshot(label=args.label)
        print(f"OK Snapshot: {path}")

    elif args.command == "snapshots":
        snaps = mem.list_snapshots()
        if not snaps:
            print("No snapshots available.")
        for s in snaps:
            kb = s["size_bytes"] / 1024
            print(f"[{s['created_at']}] {s['path']} ({kb:.1f} KB)")

    elif args.command == "restore":
        mem.restore(args.path)
        print(f"OK Restored from: {args.path}")

    elif args.command == "anomalies":
        anomalies = mem.anomalies(limit=args.limit)
        if not anomalies:
            print("No anomalies.")
        for a in anomalies:
            meta = a.get("metadata") or {}
            print(f"[{a['ts']}] {a['reason']} count={meta.get('count')}")

    elif args.command == "consolidate":
        report = mem.consolidate(dry_run=args.dry_run)
        mode = "DRY RUN" if report["dry_run"] else "APPLIED"
        print(f"Consolidation:    {mode}")
        print(f"Groups examined:  {report['groups_examined']}")
        print(f"Consolidated:     {report['facts_consolidated']}")
        print(f"Superseded:       {report['facts_superseded']}")
        for group in report["groups"]:
            tags = ",".join(group["tags"]) or "-"
            new_id = group["new_id"] or "(dry-run)"
            print(
                f"  {group['authority_class']} tags={tags}: "
                f"{len(group['old_ids'])} -> {new_id} "
                f"conf={group['confidence']:.2f}"
            )

    elif args.command == "doctor":
        print(f"Memory src path:  {Path(__file__).parent.parent / 'src'}")
        print(f"DB path:          {mem.db_path}")

        _doctor_conn, _doctor_should_close = mem._connect()
        try:
            _cur = _doctor_conn.cursor()
            _cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
                ("facts", "recall_snippets"),
            )
            _tables_present = {row[0] for row in _cur.fetchall()}
            for _tbl in ("facts", "recall_snippets"):
                _status = "present" if _tbl in _tables_present else "missing"
                print(f"Table {_tbl!r}: {_status}")

            s = mem.stats()
            print(f"Active facts:     {s['active_facts']}")
            print(f"Lessons:          {s['lessons']}")

            if "recall_snippets" in _tables_present:
                _cur.execute("SELECT COUNT(*) FROM recall_snippets")
                _snippet_count = _cur.fetchone()[0]
            else:
                _snippet_count = 0
            print(f"Snippets:         {_snippet_count}")
        finally:
            if _doctor_should_close:
                _doctor_conn.close()

        plugin_path = Path(__file__).parents[3] / "plugin" / "__init__.py"
        plugin_status = "present" if plugin_path.exists() else "missing"
        print(f"Plugin file:      {plugin_path} ({plugin_status})")

    elif args.command == "snippet":
        if args.snippet_command == "add":
            snippet_id = mem.remember_snippet(
                args.content,
                source=args.source,
                session_id=args.session,
            )
            print(f"OK [{snippet_id}] Snippet saved")
        elif args.snippet_command == "search":
            snippets = mem.search_snippets(
                args.query,
                limit=args.limit,
                session_id=args.session,
            )
            if not snippets:
                print("No snippets found.")
            for snippet in snippets:
                session = f" session={snippet.session_id}" if snippet.session_id else ""
                print(
                    f"[{snippet.id}] ({snippet.source}{session}) "
                    f"{snippet.content[:100]}"
                )


if __name__ == "__main__":
    main()
