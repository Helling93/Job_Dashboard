"""Generiert docs/index.html aus dem aktuellen state.json (für GitHub Pages)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from geo import COUNTRY_LABELS, COUNTRY_ORDER

TEMPLATE_DIR = Path(__file__).parent / "templates"

_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

# Wert von data-countries bei Jobs ohne erkanntes Land, z.B. "Mehrere Standorte" bei Siemens (Filter-Chip "Mehrere / unklar")
UNKNOWN_COUNTRY = "unbekannt"


def _status_class(status: str) -> str:
    return (status or "neu").strip().lower().replace(" ", "_")


def _format_last_run(value: str | None) -> str:
    """ISO-Zeitstempel (z.B. 2026-08-05T06:46:25.554984+00:00) lesbar
    formatieren statt roh mit 'T' und Offset ohne Leerzeichen anzuzeigen."""
    if not value:
        return "noch nie"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%d.%m.%Y %H:%M:%S UTC")


def _format_date_posted(value: str | None) -> str | None:
    """Manche Firmen (z.B. BMW) liefern das Datum als rohes YYYYMMDD statt
    lesbarem Text - hier für die Anzeige in DD.MM.YYYY umwandeln."""
    if not value:
        return value
    m = _YYYYMMDD_RE.match(value.strip())
    if not m:
        return value
    year, month, day = m.groups()
    return f"{day}.{month}.{year}"


def _sorted_jobs(jobs: dict, key: str, reverse: bool = True) -> list[dict]:
    return sorted(jobs, key=lambda j: j.get(key) or "", reverse=reverse)


def build_dashboard_context(
    state: dict,
    errors: dict[str, str],
    new_jobs_count: int,
    repo: str | None = None,
    branch: str | None = None,
) -> dict:
    companies_open: dict[str, list[dict]] = {}
    company_stale: dict[str, str] = {}
    applications: list[dict] = []
    total_open = 0
    run_day = (state.get("last_run") or "")[:10]

    for company_name, data in sorted(state.get("companies", {}).items()):
        open_jobs = []
        # Letzter erfolgreicher Scrape liegt vor dem letzten Lauf -> Firma
        # schlägt gerade fehl, ihre Stellen sind evtl. nicht mehr aktuell.
        last_success = data.get("last_success")
        if last_success and run_day and last_success < run_day:
            company_stale[company_name] = last_success
        for jid, record in data.get("jobs", {}).items():
            codes = [c for c in COUNTRY_ORDER if c in (record.get("countries") or [])]
            entry = {
                **record,
                "id": jid,
                "company": company_name,
                "status_class": _status_class(record.get("status")),
                "date_posted": _format_date_posted(record.get("date_posted")),
                # Land: für Badge (country_pills) und Filter (countries_attr)
                "country_pills": [(c, COUNTRY_LABELS[c]) for c in codes],
                "countries_attr": " ".join(codes) if codes else UNKNOWN_COUNTRY,
                "status_date": (record.get("status_changed_at") or "")[:10] or None,
            }
            if (record.get("status") or "").strip().lower() == "beworben":
                # Bewerbungen stehen gesammelt in "Meine Bewerbungen" statt in
                # der Liste offener Stellen - und bleiben dort auch, wenn die
                # Ausschreibung offline geht (state.json löscht nie Jobs).
                applications.append(entry)
            elif not record.get("disappeared_at"):
                # Sonstige verschwundene Jobs sind nur noch Rauschen.
                open_jobs.append(entry)

        if open_jobs:
            companies_open[company_name] = _sorted_jobs(open_jobs, "first_seen")
            total_open += len(open_jobs)

    # Laufende Bewerbungen (Ausschreibung noch online) zuerst, dann nach Bewerbungsdatum
    applications.sort(key=lambda j: j.get("status_changed_at") or j.get("first_seen") or "", reverse=True)
    applications.sort(key=lambda j: bool(j.get("disappeared_at")))

    # Länder-Chips nur für Länder, die bei offenen Jobs tatsächlich vorkommen
    present = {code for jobs in companies_open.values() for j in jobs for code, _ in j["country_pills"]}
    country_filters = [(c, COUNTRY_LABELS[c]) for c in COUNTRY_ORDER if c in present]
    if any(j["countries_attr"] == UNKNOWN_COUNTRY for jobs in companies_open.values() for j in jobs):
        country_filters.append((UNKNOWN_COUNTRY, "Mehrere / unklar"))

    return {
        "country_filters": country_filters,
        "last_run": _format_last_run(state.get("last_run")),
        "total_open": total_open,
        "company_count": len(state.get("companies", {})),
        "new_since_last_run": new_jobs_count,
        "errors": errors,
        "companies_open": companies_open,
        "company_stale": company_stale,
        "applications": applications,
        "sync_repo": repo,
        "sync_branch": branch or "main",
    }


def render_dashboard(
    state: dict,
    errors: dict[str, str],
    new_jobs_count: int,
    repo: str | None = None,
    branch: str | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")
    context = build_dashboard_context(state, errors, new_jobs_count, repo, branch)
    return template.render(**context)


def write_dashboard(
    state: dict,
    errors: dict[str, str],
    new_jobs_count: int,
    out_path: str | Path,
    repo: str | None = None,
    branch: str | None = None,
) -> None:
    html = render_dashboard(state, errors, new_jobs_count, repo, branch)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
