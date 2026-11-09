"""Persistiert den Scan-Stand (data/state.json) und berechnet Diffs zwischen Läufen."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scraper import Job

logger = logging.getLogger(__name__)

DEFAULT_STATUS = "neu"
# Erst wenn ein Job so viele Läufe IN FOLGE fehlt, gilt er als verschwunden.
# Paginierte Seiten (v.a. Rheinmetall) sortieren nicht stabil - beim
# Durchblättern rutschen einzelne Jobs zwischen zwei Seitenaufrufen auf eine
# schon geholte Seite und werden in diesem Lauf übersehen. Ohne Karenz
# verschwinden sie dann für einen Tag aus dem Dashboard und tauchen am
# nächsten Tag wieder auf.
MISSED_RUNS_BEFORE_DISAPPEARED = 2
# Schlägt das Scrapen einer Firma dauerhaft fehl (Seite umgezogen, Selektoren
# kaputt) oder fliegt sie aus der config, ruft niemand mehr update_company für
# sie auf - ihre Jobs blieben dann ewig "offen" (so z.B. LSS nach dem Umzug der
# Karriereseite: wochenlang 404, Stellen standen trotzdem im Dashboard). Nach so
# vielen Tagen ohne Sichtung gilt ein Job daher auch ohne erfolgreichen Lauf
# als verschwunden. Wird die Firma wieder erfolgreich gescraped, tauchen noch
# vorhandene Stellen samt Status wieder auf.
STALE_AFTER_DAYS = 7


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
    existing["last_success"] = run_date

    seen_ids = set()
    new_jobs: list[dict] = []

    for job in scraped_jobs:
        jid = job_id_for(company_name, job)
        seen_ids.add(jid)

        if jid in existing_jobs:
            record = existing_jobs[jid]
            record["last_seen"] = run_date
            record["disappeared_at"] = None
            record.pop("missed_runs", None)
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
            missed = record.get("missed_runs", 0) + 1
            if missed < MISSED_RUNS_BEFORE_DISAPPEARED:
                record["missed_runs"] = missed
                continue
            record.pop("missed_runs", None)
            record["disappeared_at"] = run_date
            disappeared_jobs.append({"id": jid, "company": company_name, **record})

    return new_jobs, disappeared_jobs


def expire_stale_jobs(state: dict, run_date: str, max_age_days: int = STALE_AFTER_DAYS) -> list[dict]:
    """Markiert offene Jobs, die seit `max_age_days` nicht mehr gesehen wurden,
    als verschwunden (siehe STALE_AFTER_DAYS). Gibt die betroffenen Jobs zurück."""
    cutoff = (datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    expired: list[dict] = []
    for company_name, data in state.get("companies", {}).items():
        for jid, record in data.get("jobs", {}).items():
            if record.get("disappeared_at") is None and (record.get("last_seen") or "") < cutoff:
                record.pop("missed_runs", None)
                record["disappeared_at"] = run_date
                expired.append({"id": jid, "company": company_name, **record})
    return expired


def touch_last_run(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    """UTC-Zeitstempel im selben Format wie JavaScripts Date.toISOString()."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def merge_states(base: dict, ours: dict, theirs: dict) -> dict:
    """Feldweiser Drei-Wege-Merge von state.json.

    base   = Stand beim Checkout (vor dem Scraper-Lauf)
    ours   = Ergebnis dieses Scraper-Laufs
    theirs = aktueller Stand auf main (u.a. Status-Klicks aus dem Dashboard,
             die während des Laufs per GitHub-API committed wurden)

    Ein zeilenbasierter git-Merge kollidiert hier leicht (last_seen und status
    liegen im JSON dicht beieinander). Feldweise gilt: Alles, was der Scraper
    gegenüber `base` verändert hat, kommt auf den Stand von `theirs` drauf -
    ausgenommen `status`: Hat jemand den Status währenddessen geändert, gewinnt
    dessen Änderung (auch gegen ein automatisches Ausblenden durch den Scraper).
    """
    merged = copy.deepcopy(theirs)
    merged["last_run"] = ours.get("last_run", merged.get("last_run"))

    for cname, ocomp in ours.get("companies", {}).items():
        bjobs = base.get("companies", {}).get(cname, {}).get("jobs", {})
        tcomp = merged.setdefault("companies", {}).setdefault(cname, {"jobs": {}})
        for key, value in ocomp.items():  # Firmen-Felder wie last_success - nur der Scraper schreibt sie
            if key != "jobs":
                tcomp[key] = copy.deepcopy(value)
        tjobs = tcomp.setdefault("jobs", {})
        for jid, ojob in ocomp.get("jobs", {}).items():
            bjob = bjobs.get(jid)
            tjob = tjobs.get(jid)
            if tjob is None:
                if bjob is None:  # vom Scraper neu angelegt
                    tjobs[jid] = copy.deepcopy(ojob)
                continue  # sonst: zwischenzeitlich entfernt -> nicht wiederbeleben
            for field in set(ojob) | set(bjob or {}):
                if field == "status":
                    continue
                if field in ojob:
                    if bjob is None or ojob[field] != bjob.get(field):
                        tjob[field] = copy.deepcopy(ojob[field])
                else:  # Scraper hat das Feld entfernt (z.B. missed_runs)
                    tjob.pop(field, None)
            scraper_changed_status = bjob is not None and ojob.get("status") != bjob.get("status")
            user_untouched_status = bjob is not None and tjob.get("status") == bjob.get("status")
            if scraper_changed_status and user_untouched_status:
                tjob["status"] = ojob["status"]  # Scraper-Änderung (Auto-Ausblenden), Nutzer hat nichts angefasst
    return merged
