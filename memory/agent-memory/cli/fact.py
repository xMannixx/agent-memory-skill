#!/usr/bin/env python3
"""CLI wrapper für AgentMemory — Hermes Edition."""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from memory import AgentMemory, AUTHORITY_POLICY


def main():
    parser = argparse.ArgumentParser(description="Hermes Agent Memory CLI")
    parser.add_argument("--db", help="Datenbankpfad", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    add_p = subparsers.add_parser("add", help="Fakt speichern")
    add_p.add_argument("content", help="Der Fakt")
    add_p.add_argument("--tags", "-t", nargs="+", default=[], help="Tags")
    add_p.add_argument("--source", "-s", default="conversation",
                       choices=["conversation", "observation", "inference"])
    add_p.add_argument("--confidence", "-c", type=float, default=0.9)
    add_p.add_argument("--authority", "-a", default="evidence",
                       choices=list(AUTHORITY_POLICY.keys()),
                       help="Authority-Klasse: identity/preference/evidence/authorization")
    add_p.add_argument("--expires", "-e", type=int, help="Ablauf in Tagen")

    # recall
    recall_p = subparsers.add_parser("recall", help="Fakten suchen")
    recall_p.add_argument("query")
    recall_p.add_argument("--limit", "-n", type=int, default=10)
    recall_p.add_argument("--tags", "-t", nargs="+")
    recall_p.add_argument("--authority", "-a", default=None,
                          choices=list(AUTHORITY_POLICY.keys()))

    # list
    list_p = subparsers.add_parser("list", help="Alle Fakten auflisten")
    list_p.add_argument("--tags", "-t", nargs="+")
    list_p.add_argument("--limit", "-n", type=int, default=20)
    list_p.add_argument("--authority", "-a", default=None,
                        choices=list(AUTHORITY_POLICY.keys()))

    # supersede
    sup_p = subparsers.add_parser("supersede", help="Fakt ersetzen")
    sup_p.add_argument("fact_id")
    sup_p.add_argument("new_content")
    sup_p.add_argument("--authority", "-a", default="evidence",
                       choices=list(AUTHORITY_POLICY.keys()))

    # forget
    subparsers.add_parser("forget-stale", help="Abgelaufene Fakten löschen (Policy-TTL)")

    # stats
    subparsers.add_parser("stats", help="Statistiken")

    # learn
    learn_p = subparsers.add_parser("learn", help="Lektion speichern")
    learn_p.add_argument("action")
    learn_p.add_argument("context")
    learn_p.add_argument("outcome", choices=["positive", "negative", "neutral"])
    learn_p.add_argument("insight")

    # lessons
    lessons_p = subparsers.add_parser("lessons", help="Lektionen abrufen")
    lessons_p.add_argument("--context", "-c")
    lessons_p.add_argument("--outcome", "-o",
                           choices=["positive", "negative", "neutral"])
    lessons_p.add_argument("--limit", "-n", type=int, default=10)

    # audit
    audit_p = subparsers.add_parser("audit", help="Audit-Log anzeigen")
    audit_p.add_argument("--limit", "-n", type=int, default=20)
    audit_p.add_argument("--op", help="Auf einen Op-Typ filtern")

    # snapshot
    snap_p = subparsers.add_parser("snapshot", help="Snapshot der DB anlegen")
    snap_p.add_argument("--label", help="Optionales Label")

    # snapshots
    subparsers.add_parser("snapshots", help="Vorhandene Snapshots auflisten")

    # restore
    restore_p = subparsers.add_parser("restore", help="DB aus Snapshot wiederherstellen")
    restore_p.add_argument("path", help="Pfad zum Snapshot")

    # anomalies
    anom_p = subparsers.add_parser("anomalies", help="Anomalie-Eintraege anzeigen")
    anom_p.add_argument("--limit", "-n", type=int, default=10)

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
            print(f"VERWORFEN — Authority-Policy oder Rebound-Schutz hat gegriffen")

    elif args.command == "recall":
        facts = mem.recall(args.query, limit=args.limit, tags=args.tags,
                           authority_class=args.authority)
        if not facts:
            print("Keine Treffer.")
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
            print(f"OK [{new_id}] ersetzt {args.fact_id}")
        else:
            print(f"FEHLER — Ersatz wurde durch Policy verworfen")

    elif args.command == "forget-stale":
        result = mem.forget_stale()
        total = sum(result.values())
        print(f"Gelöscht: {total} Facts")
        for cls, count in result.items():
            print(f"  {cls}: {count}")

    elif args.command == "stats":
        s = mem.stats()
        print(f"Aktive Facts:     {s['active_facts']}")
        print(f"Superseded:       {s['superseded_facts']}")
        print(f"Lektionen:        {s['lessons']}")
        print(f"Entities:         {s['entities']}")
        print(f"Rebound aktiv:    {s['rebound_active']}")
        print(f"Rebound Rest:     {s['rebound_remaining']}")
        print(f"Session Writes:   {s['session_writes']}")
        print(f"Recalls:          {s['recalls']}")
        latency = s.get("recall_latency_ms", {})
        if latency.get("count"):
            print(
                "Recall Latenz:    "
                f"avg={latency['avg']:.2f}ms "
                f"p50={latency['p50']:.2f}ms "
                f"p95={latency['p95']:.2f}ms "
                f"max={latency['max']:.2f}ms"
            )
        else:
            print("Recall Latenz:    keine Daten")
        print(
            f"Stale Facts:      {s['stale_facts']} "
            f"({s['stale_ratio']:.1%})"
        )
        print(f"Superseded Ratio: {s['superseded_ratio']:.1%}")
        print(f"Nach Klasse:")
        for cls, count in s.get("by_class", {}).items():
            ratio = s.get("by_class_ratio", {}).get(cls, 0.0)
            print(f"  {cls}: {count} ({ratio:.1%})")

    elif args.command == "learn":
        lid = mem.learn(args.action, args.context, args.outcome, args.insight)
        print(f"OK [{lid}] Lektion gespeichert")

    elif args.command == "lessons":
        lessons = mem.get_lessons(context=args.context, outcome=args.outcome,
                                  limit=args.limit)
        if not lessons:
            print("Keine Lektionen gefunden.")
        for l in lessons:
            print(f"[{l.id}] [{l.outcome}] {l.action} → {l.insight}")

    elif args.command == "audit":
        entries = mem.get_audit(limit=args.limit, op=args.op)
        if not entries:
            print("Keine Audit-Eintraege.")
        for e in entries:
            flag = "OK " if e["accepted"] else "REJ"
            reason = f" reason={e['reason']}" if e["reason"] else ""
            fid = f" fact={e['fact_id']}" if e["fact_id"] else ""
            cls = f" {e['authority_class']}" if e["authority_class"] else ""
            print(f"[{e['ts']}] {flag} {e['op']}{cls}{fid}{reason}")

    elif args.command == "snapshot":
        path = mem.snapshot(label=args.label)
        print(f"OK Snapshot: {path}")

    elif args.command == "snapshots":
        snaps = mem.list_snapshots()
        if not snaps:
            print("Keine Snapshots vorhanden.")
        for s in snaps:
            kb = s["size_bytes"] / 1024
            print(f"[{s['created_at']}] {s['path']} ({kb:.1f} KB)")

    elif args.command == "restore":
        mem.restore(args.path)
        print(f"OK Wiederhergestellt aus: {args.path}")

    elif args.command == "anomalies":
        anomalies = mem.anomalies(limit=args.limit)
        if not anomalies:
            print("Keine Anomalien.")
        for a in anomalies:
            meta = a.get("metadata") or {}
            print(f"[{a['ts']}] {a['reason']} count={meta.get('count')}")


if __name__ == "__main__":
    main()
