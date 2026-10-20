"""Prüft die Detailseiten neuer Stellen auf das geforderte Qualifikationsniveau.

Stellen, bei denen (erkennbar) nur eine Berufsausbildung verlangt wird, aber
kein Studium, werden automatisch auf Status "kein interesse" gesetzt - analog
zu den globalen Titel-Keywords, nur eben anhand der Stellenbeschreibung.

Design-Entscheidungen (Laufzeit + Fehlalarme):
  * Nur Jobs mit Status "neu", die noch nie geprüft wurden. Das Ergebnis steht
    danach im State (`requirement`) -> jede Stelle wird höchstens einmal
    abgerufen, im Normalbetrieb also nur die paar Neuzugänge pro Tag.
  * Ingenieur-/Entwickler-/Manager-Titel werden gar nicht erst abgerufen
    (gelten als Studium-Niveau) - spart den Großteil der Seitenaufrufe.
  * Hartes Limit pro Lauf (Anzahl + Zeitbudget); der Rest folgt am nächsten Tag.
  * Konservativ: Ausgeblendet wird nur bei klarem Treffer "Ausbildung" UND
    keinerlei Studium-Hinweis auf der Seite. Im Zweifel bleibt die Stelle.
"""

from __future__ import annotations

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from scraper import BOT_CHALLENGE_MARKERS, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 500          # darunter gilt eine Seite als nicht (richtig) geladen
MAX_ATTEMPTS = 2              # Fehlversuche pro Job, danach "unreadable"
MAX_CONSECUTIVE_FAILURES = 3  # so viele Fehler in Folge -> Firma für diesen Lauf abbrechen
PAGE_DELAY_SECONDS = 0.8      # kleine Pause zwischen Seitenaufrufen derselben Firma

# Titel, die erfahrungsgemäß Hochschul-Niveau bedeuten -> kein Abruf nötig.
DEGREE_TITLE_HINTS = (
    "ingenieur", "engineer", "entwickl", "developer", "architect", "scientist",
    "forsch", "researcher", "physiker", "wissenschaft", "informatiker",
    "consultant", "analyst", "manager", "leiter", "lead", "head of", "director",
)

# Alles, was auf ein Studium hindeutet. Bewusst weit gefasst: ein Fehltreffer
# (z.B. "6 degrees of freedom", "Akademie") lässt die Stelle nur stehen - ein
# übersehener Studium-Hinweis dagegen würde eine gute Stelle ausblenden.
_DEGREE_RE = re.compile(
    r"studium|studiengang|studiert|hochschul|fachhochschul|universit(?:ät|aet|y|ies)"
    r"|bachelor|diplom(?!at)|promotion|akademi|ingenieurabschluss|ingenieurwesen"
    r"|abschluss\s+als\s+\w*ingenieur|degree|\bb\.?\s?sc\b|\bm\.?\s?sc\b|\bphd\b|\bmba\b"
    r"|\bmaster(?:'s|’s|abschluss|studium|-abschluss|\s+of|\s+in|\s+degree)"
    r"|\bm\.?\s?eng\b|\bb\.?\s?eng\b|dipl\.-ing|\bbsc\b|\bmsc\b",
    re.IGNORECASE,
)

# Anforderungs-typische Formulierungen für eine Berufsausbildung (nicht das
# bloße Wort "Ausbildung", das auch in Benefits-Listen vorkommt).
_VOCATIONAL_RE = re.compile(
    r"berufsausbildung|berufsabschluss|abgeschlossene[rn]?\s+(?:\w+\s+){0,4}ausbildung"
    r"|ausbildung\s+(?:als|zum|zur|in\s+(?:der|einem|einer)|im)\b"
    r"|facharbeiter|gesellenbrief|gesellenprüfung|meisterbrief|industriemeister"
    r"|techniker|fachkraft|ausbildungsberuf|technische[rn]?\s+ausbildung"
    r"|kaufmännische[rn]?\s+ausbildung|ausgebildete[rn]?"
    r"|vocational|apprenticeship|completed\s+(?:\w+\s+){0,3}(?:training|apprenticeship)"
    r"|trained\s+as",
    re.IGNORECASE,
)


def title_implies_degree(title: str) -> bool:
    t = (title or "").lower()
    return any(hint in t for hint in DEGREE_TITLE_HINTS)


def classify_requirements(text: str) -> tuple[str, str | None]:
    """Gibt (Urteil, Beleg) zurück. Urteil: "degree" | "vocational" | "unknown".

    "degree" gewinnt immer - schon ein Studium-Hinweis lässt die Stelle stehen.
    "vocational" nur, wenn ein Ausbildungs-Hinweis da ist und gar kein Studium.
    """
    m = _DEGREE_RE.search(text)
    if m:
        return "degree", m.group(0)
    m = _VOCATIONAL_RE.search(text)
    if m:
        ctx = text[max(0, m.start() - 40): m.end() + 40]
        return "vocational", " ".join(ctx.split())
    return "unknown", None


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _looks_like_challenge(text: str) -> bool:
    return any(marker in text[:600] for marker in BOT_CHALLENGE_MARKERS)


def _fetch_requests(items: list[tuple[str, str]], deadline: float) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    failures = 0
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        for jid, link in items:
            if time.monotonic() > deadline:
                break
            try:
                resp = session.get(link, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                text = _clean_text(resp.text)
                out[jid] = text if len(text) >= MIN_TEXT_CHARS and not _looks_like_challenge(text) else None
            except Exception as e:  # noqa: BLE001
                logger.warning("Detailseite nicht ladbar (%s): %s", link, e)
                out[jid] = None
            failures = failures + 1 if out[jid] is None else 0
            if failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning("Zu viele Fehler in Folge - breche Detailabruf für diese Firma ab.")
                break
            time.sleep(PAGE_DELAY_SECONDS)
    return out


def _fetch_playwright(
    company: dict, items: list[tuple[str, str]], deadline: float
) -> dict[str, str | None]:
    from playwright.sync_api import sync_playwright

    out: dict[str, str | None] = {}
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            page = browser.new_page(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 800}, locale="de-DE"
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            for jid, link in items:
                if time.monotonic() > deadline:
                    break
                try:
                    page.goto(link, timeout=REQUEST_TIMEOUT * 1000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_function(
                            "document.body && document.body.innerText.trim().length > 800",
                            timeout=8000,
                        )
                    except Exception:
                        page.wait_for_timeout(1500)
                    if company.get("flatten_shadow_dom"):
                        page.evaluate(
                            """(function flatten(root) {
                                root.querySelectorAll('*').forEach(function(el) {
                                    if (el.shadowRoot) {
                                        flatten(el.shadowRoot);
                                        el.innerHTML = el.shadowRoot.innerHTML + el.innerHTML;
                                    }
                                });
                            })(document);"""
                        )
                    text = _clean_text(page.content())
                    ok = len(text) >= MIN_TEXT_CHARS and not _looks_like_challenge(text)
                    out[jid] = text if ok else None
                except Exception as e:  # noqa: BLE001
                    logger.warning("Detailseite nicht ladbar (%s): %s", link, e)
                    out[jid] = None
                failures = failures + 1 if out[jid] is None else 0
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning("Zu viele Fehler in Folge - breche Detailabruf für diese Firma ab.")
                    break
                time.sleep(PAGE_DELAY_SECONDS)
        finally:
            browser.close()
    return out


def fetch_detail_texts(
    company: dict, items: list[tuple[str, str]], deadline: float
) -> dict[str, str | None]:
    """Holt den Text der Detailseiten ([(job_id, link)]). Wert None = nicht lesbar.

    Jobs, für die wegen Zeitbudget/Abbruch nichts abgerufen wurde, fehlen im Ergebnis.
    Nutzt die Methode der Firma (Playwright-Firmen sind oft gegen requests
    geschützt - ein Versuch per requests würde dort nur in Timeouts laufen)."""
    method = company.get("detail_method") or company.get("method", "requests")
    if method == "playwright":
        return _fetch_playwright(company, items, deadline)
    result = _fetch_requests(items, deadline)
    # SPA-Detailseiten liefern per requests oft nur eine leere Shell -> gezielt
    # die nicht lesbaren Jobs per Playwright nachholen.
    missing = [(jid, link) for jid, link in items if jid in result and result[jid] is None]
    if missing and company.get("detail_fallback_playwright", True):
        retry = _fetch_playwright(company, missing, deadline)
        result.update({jid: text for jid, text in retry.items() if text is not None})
    return result


def apply_requirement_filter(state: dict, companies: list[dict], cfg: dict) -> set[str]:
    """Prüft neue/ungeprüfte Jobs und blendet reine Ausbildungs-Stellen aus.

    Ändert `state` in-place und gibt die IDs der automatisch ausgeblendeten Jobs
    zurück (damit main.py sie nicht per Telegram meldet)."""
    max_checks = cfg.get("max_checks_per_run", 60)
    deadline = time.monotonic() + cfg.get("time_budget_seconds", 420)
    hide = cfg.get("hide_vocational", True)
    by_name = {c.get("name"): c for c in companies}

    to_fetch: dict[str, list[tuple[str, str, dict]]] = {}
    title_shielded = 0
    for cname, cdata in state.get("companies", {}).items():
        company = by_name.get(cname)
        if company is None or company.get("detail_filter") is False:
            continue
        for jid, rec in cdata.get("jobs", {}).items():
            if rec.get("disappeared_at") or rec.get("status") != "neu":
                continue
            if "requirement" in rec or not rec.get("link"):
                continue
            if title_implies_degree(rec.get("title", "")):
                rec["requirement"] = "degree"
                title_shielded += 1
                continue
            to_fetch.setdefault(cname, []).append((jid, rec["link"], rec))

    # Neueste zuerst, global auf max_checks begrenzt (Rest folgt an den nächsten Tagen)
    queue = sorted(
        ((cname, jid, link, rec) for cname, lst in to_fetch.items() for jid, link, rec in lst),
        key=lambda x: x[3].get("first_seen", ""), reverse=True,
    )[:max_checks]
    per_company: dict[str, list[tuple[str, str, dict]]] = {}
    for cname, jid, link, rec in queue:
        per_company.setdefault(cname, []).append((jid, link, rec))

    hidden: set[str] = set()
    checked = unreadable = 0
    for cname, entries in per_company.items():
        texts = fetch_detail_texts(by_name[cname], [(jid, link) for jid, link, _ in entries], deadline)
        for jid, _link, rec in entries:
            if jid not in texts:
                continue  # Zeitbudget/Abbruch - beim nächsten Lauf wieder dabei
            text = texts[jid]
            if text is None:
                rec["requirement_attempts"] = rec.get("requirement_attempts", 0) + 1
                if rec["requirement_attempts"] >= MAX_ATTEMPTS:
                    rec["requirement"] = "unreadable"
                    unreadable += 1
                continue
            verdict, evidence = classify_requirements(text)
            rec["requirement"] = verdict
            checked += 1
            if verdict == "vocational":
                rec["requirement_evidence"] = evidence
                if hide:
                    rec["status"] = "kein interesse"
                    rec["auto_hidden"] = "ausbildung"
                    hidden.add(jid)
                    logger.info("Ausbildung-Filter: %s / %s ausgeblendet (…%s…)",
                                cname, rec.get("title"), evidence)

    remaining = max(0, sum(len(v) for v in to_fetch.values()) - len(queue))
    logger.info(
        "Anforderungs-Check: %d per Titel als Studium-Niveau eingestuft, %d Detailseiten geprüft, "
        "%d ausgeblendet, %d nicht lesbar, %d noch offen für spätere Läufe.",
        title_shielded, checked, len(hidden), unreadable, remaining,
    )
    return hidden
