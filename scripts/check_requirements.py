"""Trockenlauf des Anforderungs-Checks: zeigt, wie qualifications.py Jobs einstufen
würde - OHNE data/state.json zu verändern.

Usage:
    python scripts/check_requirements.py [--statuses neu,interessant,beworben]
                                         [--company NAME] [--limit N] [--include-shielded]

Zum Prüfen der Treffsicherheit: mit --statuses interessant,beworben laufen
lassen - dort darf KEINE Stelle als "vocational" auftauchen (sonst wäre sie
fälschlich ausgeblendet worden).
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import yaml  # noqa: E402

import qualifications  # noqa: E402
import storage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--statuses", default="neu")
    ap.add_argument("--company")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-shielded", action="store_true",
                    help="auch Jobs prüfen, die per Titel (Engineer/Ingenieur/...) übersprungen würden")
    ap.add_argument("--budget", type=int, default=1500, help="Zeitbudget in Sekunden")
    args = ap.parse_args()

    statuses = {s.strip() for s in args.statuses.split(",")}
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    by_name = {c["name"]: c for c in cfg["companies"]}
    state = storage.load_state("data/state.json")

    todo: dict[str, list] = collections.defaultdict(list)
    shielded = 0
    for cname, cdata in state["companies"].items():
        company = by_name.get(cname)
        if company is None or company.get("detail_filter") is False:
            continue
        if args.company and args.company.lower() not in cname.lower():
            continue
        for jid, rec in cdata["jobs"].items():
            if rec.get("disappeared_at") or rec.get("status") not in statuses or not rec.get("link"):
                continue
            if not args.include_shielded and qualifications.title_implies_degree(rec["title"]):
                shielded += 1
                continue
            todo[cname].append((jid, rec))

    total = sum(len(v) for v in todo.values())
    print(f"{total} Detailseiten zu prüfen ({shielded} per Titel übersprungen)\n")
    deadline = time.monotonic() + args.budget
    counts: collections.Counter = collections.Counter()
    done = 0
    for cname, entries in todo.items():
        if args.limit:
            entries = entries[: max(0, args.limit - done)]
        if not entries:
            break
        texts = qualifications.fetch_detail_texts(
            by_name[cname], [(jid, rec["link"]) for jid, rec in entries], deadline
        )
        for jid, rec in entries:
            done += 1
            text = texts.get(jid)
            if text is None:
                verdict, evidence = "unreadable", None
            else:
                verdict, evidence = qualifications.classify_requirements(text)
            counts[(rec["status"], verdict)] += 1
            mark = {"vocational": "AUSBILDUNG", "degree": "studium   ", "unknown": "unklar    ",
                    "unreadable": "nicht lesb"}[verdict]
            print(f"[{mark}] {cname[:22]:22} | {rec['status']:14} | {rec['title'][:60]:60} | {evidence or ''}")
    print("\nZusammenfassung (Status, Urteil): Anzahl")
    for (status, verdict), n in sorted(counts.items()):
        print(f"  {status:15} {verdict:11} {n}")


if __name__ == "__main__":
    main()
