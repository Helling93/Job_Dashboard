"""Sendet Benachrichtigungen über die Telegram Bot API.

Token und Chat-ID kommen ausschließlich aus Umgebungsvariablen
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) - siehe README für Setup via
GitHub Secrets bzw. lokale .env für Tests. Sie werden nirgendwo im Code
oder in Config-Dateien hinterlegt.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4000  # Telegram-Limit ist 4096, etwas Puffer lassen


def _send(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        logger.warning(
            "Telegram-Benachrichtigung übersprungen: TELEGRAM_BOT_TOKEN oder "
            "TELEGRAM_CHAT_ID nicht gesetzt."
        )
        return False

    url = TELEGRAM_API_URL.format(token=token)
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Telegram-Nachricht konnte nicht gesendet werden: %s", e)
        return False


def _chunk_text(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def notify_new_jobs(token: str, chat_id: str, new_jobs: list[dict]) -> bool:
    if not new_jobs:
        return False

    by_company: dict[str, list[dict]] = {}
    for job in new_jobs:
        by_company.setdefault(job["company"], []).append(job)

    lines = [f"🆕 <b>{len(new_jobs)} neue Stelle(n) gefunden</b>", ""]
    for company, jobs in by_company.items():
        lines.append(f"<b>{company}</b>")
        for job in jobs:
            loc = f" – {job['location']}" if job.get("location") else ""
            lines.append(f'• <a href="{job["link"]}">{job["title"]}</a>{loc}')
        lines.append("")

    text = "\n".join(lines).strip()
    return all(_send(token, chat_id, chunk) for chunk in _chunk_text(text))


def notify_errors(token: str, chat_id: str, errors: dict[str, str]) -> bool:
    if not errors:
        return False

    lines = [f"⚠️ <b>{len(errors)} Firma/Firmen konnten nicht gescraped werden</b>", ""]
    for company, message in errors.items():
        lines.append(f"<b>{company}</b>: {message}")

    text = "\n".join(lines)
    return all(_send(token, chat_id, chunk) for chunk in _chunk_text(text))
