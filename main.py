"""Orchestriert einen kompletten Scan-Lauf: scrapen → diffen → benachrichtigen → Dashboard bauen."""

from __future__ import annotations

import logging
import os
import sys

import yaml
from dotenv import load_dotenv

import dashboard
import notifier
import storage
from scraper import scrape_company

load_dotenv()  # No-op in GitHub Actions (kein .env vorhanden) - Secrets kommen dort aus os.environ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

CONFIG_PATH = os.environ.get("JOB_WATCHER_CONFIG", "config.yaml")
STATE_PATH = os.environ.get("JOB_WATCHER_STATE", "data/state.json")
DASHBOARD_PATH = os.environ.get("JOB_WATCHER_DASHBOARD", "docs/index.html")


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run() -> int:
    config = load_config()
    companies = config.get("companies", [])
    settings = config.get("settings", {})

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    state = storage.load_state(STATE_PATH)
    run_date = storage.today_str()

    all_new_jobs: list[dict] = []
    all_disappeared: list[dict] = []
    errors: dict[str, str] = {}

    if not companies:
        logger.warning("Keine Firmen in %s konfiguriert.", CONFIG_PATH)

    for company in companies:
        name = company.get("name", "Unbekannt")
        logger.info("Scrape %s (%s)...", name, company.get("url"))
        jobs, error = scrape_company(company)

        if error:
            errors[name] = error
            continue

        new_jobs, disappeared = storage.update_company(state, name, jobs, run_date)
        all_new_jobs.extend(new_jobs)
        if settings.get("track_disappeared", True):
            all_disappeared.extend(disappeared)

        logger.info(
            "%s: %d Jobs gefunden, %d neu, %d verschwunden",
            name, len(jobs), len(new_jobs), len(disappeared),
        )

    storage.touch_last_run(state)
    storage.save_state(STATE_PATH, state)

    # GITHUB_REPOSITORY/GITHUB_REF_NAME werden von Actions automatisch gesetzt
    # und aktivieren im Dashboard den optionalen GitHub-Sync für Statusänderungen
    # (siehe README "Geräteübergreifende Synchronisation"). Lokal nicht gesetzt
    # -> Sync-UI im Dashboard bleibt deaktiviert.
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME")

    dashboard.write_dashboard(state, errors, len(all_new_jobs), DASHBOARD_PATH, repo, branch)
    logger.info("Dashboard geschrieben nach %s", DASHBOARD_PATH)

    if settings.get("notify_on_new_jobs", True) and all_new_jobs:
        sent = notifier.notify_new_jobs(bot_token, chat_id, all_new_jobs)
        logger.info(
            "Telegram-Benachrichtigung für %d neue Jobs %s.",
            len(all_new_jobs), "gesendet" if sent else "übersprungen/fehlgeschlagen",
        )

    if settings.get("notify_on_error", True) and errors:
        sent = notifier.notify_errors(bot_token, chat_id, errors)
        logger.info(
            "Telegram-Warnung für %d Fehler %s.",
            len(errors), "gesendet" if sent else "übersprungen/fehlgeschlagen",
        )

    logger.info(
        "Lauf abgeschlossen. %d neu, %d verschwunden, %d Fehler.",
        len(all_new_jobs), len(all_disappeared), len(errors),
    )

    # Exit-Code bleibt 0, auch bei einzelnen Firmenfehlern - der Lauf als
    # Ganzes ist erfolgreich, damit der Workflow trotzdem committed/deployed.
    return 0


if __name__ == "__main__":
    sys.exit(run())
