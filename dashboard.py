"""Generiert docs/index.html aus dem aktuellen state.json (für GitHub Pages)."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _status_class(status: str) -> str:
    return (status or "neu").strip().lower().replace(" ", "_")


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
    companies_gone: dict[str, list[dict]] = {}
    total_open = 0

    for company_name, data in sorted(state.get("companies", {}).items()):
        open_jobs = []
        gone_jobs = []
        for jid, record in data.get("jobs", {}).items():
            entry = {
                **record,
                "id": jid,
                "company": company_name,
                "status_class": _status_class(record.get("status")),
                "date_posted": _format_date_posted(record.get("date_posted")),
            }
            if record.get("disappeared_at"):
                # Verschwundene Jobs interessieren nur noch, wenn tatsächlich
                # eine Bewerbung lief - alles andere ist nur Rauschen.
                if (record.get("status") or "").strip().lower() == "beworben":
                    gone_jobs.append(entry)
            else:
                open_jobs.append(entry)

        if open_jobs:
            companies_open[company_name] = _sorted_jobs(open_jobs, "first_seen")
            total_open += len(open_jobs)
        if gone_jobs:
            companies_gone[company_name] = _sorted_jobs(gone_jobs, "disappeared_at")

    return {
        "last_run": state.get("last_run") or "noch nie",
        "total_open": total_open,
        "company_count": len(state.get("companies", {})),
        "new_since_last_run": new_jobs_count,
        "errors": errors,
        "companies_open": companies_open,
        "companies_gone": companies_gone,
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
