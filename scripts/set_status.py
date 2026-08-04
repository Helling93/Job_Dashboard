"""CLI-Helfer, um den manuellen Status eines Jobs in data/state.json zu ändern.

Usage:
    python scripts/set_status.py --list [firma-filter]
    python scripts/set_status.py <job_id> <status>

Status ist frei wählbar, empfohlen: "neu", "beworben", "kein interesse".
Nach der Änderung: git add data/state.json && git commit && git push
(oder das Feld direkt im GitHub-Web-Editor bearbeiten - macht dasselbe).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402

STATE_PATH = "data/state.json"


def list_jobs(filter_str: str | None = None) -> None:
    state = storage.load_state(STATE_PATH)
    for company, data in sorted(state.get("companies", {}).items()):
        if filter_str and filter_str.lower() not in company.lower():
            continue
        for jid, record in data.get("jobs", {}).items():
            gone = " [verschwunden]" if record.get("disappeared_at") else ""
            print(f"{jid}  [{record.get('status')}]{gone}  {company} - {record.get('title')}")


def set_status(job_id: str, status: str) -> None:
    state = storage.load_state(STATE_PATH)
    for company, data in state.get("companies", {}).items():
        jobs = data.get("jobs", {})
        if job_id in jobs:
            jobs[job_id]["status"] = status
            storage.save_state(STATE_PATH, state)
            print(f"OK: {company} - {jobs[job_id]['title']} -> status = '{status}'")
            return
    print(f"Job-ID '{job_id}' nicht gefunden. Nutze --list zum Nachschauen.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        list_jobs(args[1] if len(args) > 1 else None)
    elif len(args) == 2:
        set_status(args[0], args[1])
    else:
        print(__doc__)
        sys.exit(1)
