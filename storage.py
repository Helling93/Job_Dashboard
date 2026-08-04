"""Persistiert den Scan-Stand (data/state.json) und berechnet Diffs zwischen Läufen."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from scraper import Job

logger = logging.getLogger(__name__)

DEFAULT_STATUS = "neu"


def job_id_for(company_name: str, job: Job) -> str:
    """Stabile ID pro Job, basierend auf Link (fällt auf Firma+Titel zurück)."""
    basis = job.link or f"{company_name}::{job.title}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"companies": {}, "last_run": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str | Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def update_company(
    state: dict, company_name: str, scraped_jobs: list[Job], run_date: str
) -> tuple[list[dict], list[dict]]:
    """Gleicht gescrapte Jobs einer Firma mit dem gespeicherten Stand ab.

    Aktualisiert `state` in-place (first_seen/last_seen/disappeared_at/status
    bleiben über Läufe hinweg erhalten). Gibt (neue_jobs, verschwundene_jobs) zurück.
    """
    companies = state.setdefault("companies", {})
    existing = companies.setdefault(company_name, {"jobs": {}})
    existing_jobs = existing.setdefault("jobs", {})

    seen_ids = set()
    new_jobs: list[dict] = []

    for job in scraped_jobs:
        jid = job_id_for(company_name, job)
        seen_ids.add(jid)

        if jid in existing_jobs:
            record = existing_jobs[jid]
            record["last_seen"] = run_date
            record["disappeared_at"] = None
            # Titel/Standort/Datum/Kategorie aktuell halten, falls sich Kleinigkeiten ändern
            record["title"] = job.title
            record["location"] = job.location
            record["date_posted"] = job.date_posted
            record["category"] = job.category
        else:
            record = {
                "title": job.title,
                "link": job.link,
                "location": job.location,
                "date_posted": job.date_posted,
                "category": job.category,
                "first_seen": run_date,
                "last_seen": run_date,
                "disappeared_at": None,
                "status": DEFAULT_STATUS,
            }
            existing_jobs[jid] = record
            new_jobs.append({"id": jid, "company": company_name, **record})

    disappeared_jobs: list[dict] = []
    for jid, record in existing_jobs.items():
        if jid not in seen_ids and record.get("disappeared_at") is None:
            record["disappeared_at"] = run_date
            disappeared_jobs.append({"id": jid, "company": company_name, **record})

    return new_jobs, disappeared_jobs


def touch_last_run(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
