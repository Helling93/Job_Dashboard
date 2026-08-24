"""Lädt Karriereseiten und extrahiert Job-Listings gemäß config.yaml."""

from __future__ import annotations

import html as html_lib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
# Ein "ehrlicher" Bot-User-Agent (z.B. "job-alert-bot") reicht bei mehreren
# Ziel-Seiten (u.a. Helsings Vercel-Bot-Schutz) allein schon, um zuverlässig
# geblockt zu werden - ein normaler Browser-UA kommt durch, ohne dass sich
# am eigentlichen Verhalten (Frequenz, Zweck) etwas ändert.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Manche Seiten (z.B. Vercel-gehostete) zeigen bei zu vielen/verdächtigen
# Anfragen kurzzeitig eine Bot-Challenge statt der echten Seite. Das ist
# meistens transient - ein erneuter Versuch nach kurzer Pause reicht i.d.R.
BOT_CHALLENGE_MARKERS = (
    "Vercel Security Checkpoint",
    "Checking your browser",
    "Just a moment...",
    "Attention Required",
)
FETCH_RETRY_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 8


class ScrapeError(Exception):
    """Wird geworfen, wenn eine Firmenseite nicht gescraped werden konnte."""


@dataclass
class Job:
    title: str
    link: str
    location: str | None = None
    date_posted: str | None = None
    category: str | None = None
    extra: dict = field(default_factory=dict)


def fetch_html(company: dict) -> str:
    """Lädt den HTML-Inhalt einer Karriereseite (requests oder playwright)."""
    method = company.get("method", "requests")
    url = company["url"]

    if url.startswith("file://"):
        # Nur für lokale Tests/Debugging von Selektoren, siehe README.
        from urllib.request import url2pathname
        from urllib.parse import urlparse

        return Path(url2pathname(urlparse(url).path)).read_text(encoding="utf-8")

    if method == "requests":
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.text

    if method == "playwright":
        # Lazy import: playwright + Browser-Binaries sind optional und nur
        # nötig, wenn tatsächlich eine Firma method: playwright nutzt.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--disable-blink-features=AutomationControlled"]
            )
            page = browser.new_page(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="de-DE",
            )
            # navigator.webdriver=true ist eines der ersten Signale, das
            # simple Bot-Checks (u.a. Vercels) prüfen - Playwright setzt es
            # standardmäßig, hier vor jedem Seitenaufruf wieder entfernen.
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            # "networkidle" hängt sich bei Seiten mit dauerhaften Hintergrund-
            # Requests (Tracking, Video, Prefetch) oft auf - domcontentloaded
            # + gezieltes Warten auf den Listen-Selektor ist robuster.
            page.goto(url, timeout=REQUEST_TIMEOUT * 1000, wait_until="domcontentloaded")

            dismiss_selectors = company.get("dismiss_selector")
            if dismiss_selectors:
                if isinstance(dismiss_selectors, str):
                    dismiss_selectors = [dismiss_selectors]
                for sel in dismiss_selectors:
                    # locator.is_visible() wartet NICHT (der timeout-Parameter
                    # wird von Playwright ignoriert - "returns immediately")
                    # - wait_for(state=...) ist der einzige Weg, wirklich auf
                    # das (ggf. erst nach domcontentloaded nachladende) Banner
                    # zu warten. Bis zu 3 Versuche, da ein anderes Overlay den
                    # Klick abfangen kann, ohne dass er sichtbar fehlschlägt.
                    banner = page.locator(sel).first
                    for _ in range(3):
                        try:
                            banner.wait_for(state="visible", timeout=4000)
                        except Exception:
                            break  # nie erschienen - nichts zum Wegklicken da
                        try:
                            banner.click(timeout=3000)
                        except Exception:
                            break
                        page.wait_for_timeout(500)
                        try:
                            banner.wait_for(state="hidden", timeout=2000)
                            break  # erfolgreich weggeklickt
                        except Exception:
                            continue  # noch sichtbar - nochmal versuchen

            if company.get("list_selector"):
                try:
                    page.wait_for_selector(company["list_selector"], timeout=15000)
                except Exception:
                    pass  # extract_jobs meldet fehlende Treffer selbst als ScrapeError

            load_more_selector = company.get("load_more_selector")
            if load_more_selector:
                max_clicks = company.get("max_load_more_clicks", 20)
                for _ in range(max_clicks):
                    button = page.locator(load_more_selector).first
                    try:
                        if not button.is_visible(timeout=1000):
                            break
                        button.click(timeout=3000)
                        page.wait_for_timeout(700)
                    except Exception:
                        break  # kein Button (mehr) da - alles geladen

            if company.get("flatten_shadow_dom"):
                # Manche Seiten (z.B. KNDS, gebaut mit Stencil.js Web
                # Components) rendern Jobs in Shadow DOM - page.content()
                # sieht davon nichts. Shadow-Root-Inhalte werden hier vor dem
                # Serialisieren in normales Light-DOM "hineinkopiert".
                page.evaluate("""
                    (function flatten(root) {
                        root.querySelectorAll('*').forEach(function(el) {
                            if (el.shadowRoot) {
                                flatten(el.shadowRoot);
                                el.innerHTML = el.shadowRoot.innerHTML + el.innerHTML;
                            }
                        });
                    })(document);
                """)

            html = page.content()
            browser.close()
            return html

    raise ScrapeError(f"Unbekannte method '{method}' für Firma {company.get('name')}")


def _looks_like_bot_challenge(html: str) -> bool:
    head = html[:2000]
    return any(marker in head for marker in BOT_CHALLENGE_MARKERS)


def fetch_html_with_retry(company: dict) -> str:
    """Wie fetch_html(), aber mit Retry bei erkannter Bot-Challenge-Seite
    (z.B. Vercel Security Checkpoint) - das ist meist transient und tritt bei
    zu vielen/verdächtigen Anfragen kurzfristig auf, nicht bei einer
    tatsächlichen Strukturänderung der Seite."""
    html = ""
    for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
        html = fetch_html(company)
        if not _looks_like_bot_challenge(html):
            return html
        logger.warning(
            "Möglicher Bot-Schutz bei %s erkannt (Versuch %d/%d) - warte %ds und versuche erneut.",
            company.get("name"), attempt, FETCH_RETRY_ATTEMPTS, FETCH_RETRY_DELAY_SECONDS,
        )
        if attempt < FETCH_RETRY_ATTEMPTS:
            time.sleep(FETCH_RETRY_DELAY_SECONDS)
    return html


def _text_or_none(el) -> str | None:
    if el is None:
        return None
    # html.unescape() als Absicherung gegen doppelt kodierte Entities (z.B.
    # "&amp;amp;" statt "&"), wie sie z.B. bmwgroup.jobs ausliefert.
    text = html_lib.unescape(el.get_text(strip=True))
    return text or None


def _resolve_el(item, selector: str | None):
    """Löst einen Selektor relativ zu `item` auf. "self" meint `item` selbst -
    nötig, wenn das Job-Element (list_selector) bereits der Link/Titel-Träger ist
    (z.B. wenn jedes Job-Listing ein <a> mit data-Attributen ist, wie bei BMW)."""
    if selector in (None, "self", ":self"):
        return item
    return item.select_one(selector)


def _field_value(item, selector: str | None, attr: str | None) -> str | None:
    """Liest ein Feld entweder aus Text (Standard) oder aus einem Attribut
    (attr gesetzt) - manche Seiten liefern Titel/Ort/Datum als data-Attribute
    statt als sichtbaren Text."""
    el = _resolve_el(item, selector)
    if el is None:
        return None
    if attr:
        val = el.get(attr)
        return html_lib.unescape(val.strip()) if val else None
    return _text_or_none(el)


def extract_jobs(html: str, company: dict) -> list[Job]:
    """Extrahiert Job-Listings aus HTML gemäß den Selektoren in der Config."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(company["list_selector"])

    if not items:
        raise ScrapeError(
            f"Selektor '{company['list_selector']}' fand keine Elemente bei "
            f"{company.get('name')} ({company.get('url')}). "
            "Seitenstruktur hat sich vermutlich geändert."
        )

    jobs: list[Job] = []
    for item in items:
        title = _field_value(item, company["title_selector"], company.get("title_attr"))
        if not title:
            logger.warning(
                "Job-Element ohne Titel übersprungen bei %s", company.get("name")
            )
            continue

        link_el = _resolve_el(item, company.get("link_selector", "a"))
        link_attr = company.get("link_attr", "href")
        raw_link = link_el.get(link_attr) if link_el else None
        if not raw_link:
            logger.warning(
                "Job '%s' ohne Link übersprungen bei %s", title, company.get("name")
            )
            continue

        link = urljoin(company.get("base_url", company["url"]), raw_link)

        location = None
        if company.get("location_selector"):
            location = _field_value(item, company["location_selector"], company.get("location_attr"))

        date_posted = None
        if company.get("date_selector"):
            date_posted = _field_value(item, company["date_selector"], company.get("date_attr"))

        category = None
        if company.get("category_selector"):
            category = _field_value(item, company["category_selector"], company.get("category_attr"))

        jobs.append(Job(title=title, link=link, location=location, date_posted=date_posted, category=category))

    return jobs


def _matches_filters(job: Job, filters: dict) -> bool:
    """Post-Filter zusätzlich zu ggf. serverseitigen Filtern (z.B. BMWs
    Standort-/Kategorie-Filter in der URL). Alle Keywords werden
    case-insensitiv als Teilstring geprüft."""
    location = (job.location or "").lower()
    title = job.title.lower()
    category = (job.category or "").lower()

    location_contains = filters.get("location_contains")
    if location_contains and not any(kw.lower() in location for kw in location_contains):
        return False

    exclude_location_contains = filters.get("exclude_location_contains")
    if exclude_location_contains and any(kw.lower() in location for kw in exclude_location_contains):
        return False

    title_contains = filters.get("title_contains")
    if title_contains and not any(kw.lower() in title for kw in title_contains):
        return False

    exclude_title_contains = filters.get("exclude_title_contains")
    if exclude_title_contains and any(kw.lower() in title for kw in exclude_title_contains):
        return False

    category_contains = filters.get("category_contains")
    if category_contains and not any(kw.lower() in category for kw in category_contains):
        return False

    exclude_category_contains = filters.get("exclude_category_contains")
    if exclude_category_contains and any(kw.lower() in category for kw in exclude_category_contains):
        return False

    return True


def _scrape_paginated(company: dict) -> list[Job]:
    """Holt mehrere Seiten (URL enthält "{page}" als Platzhalter, 1-basiert)
    und hängt die Ergebnisse aneinander. Stoppt, sobald eine Folgeseite keine
    Treffer mehr liefert, oder spätestens bei max_pages (Standard: 15)."""
    all_jobs: list[Job] = []
    max_pages = company.get("max_pages", 15)
    url_template = company["url"]

    for page in range(1, max_pages + 1):
        page_company = dict(company)
        page_company["url"] = url_template.format(page=page)
        html = fetch_html_with_retry(page_company)
        try:
            jobs = extract_jobs(html, page_company)
        except ScrapeError:
            if page == 1:
                raise
            break  # keine weiteren Treffer - Ende der Pagination erreicht
        if not jobs:
            break
        all_jobs.extend(jobs)

    return all_jobs


def scrape_company(company: dict) -> tuple[list[Job], str | None]:
    """Scraped eine einzelne Firma (ggf. mehrseitig). Gibt (jobs, error) zurück - wirft nicht."""
    try:
        if company.get("paginate"):
            jobs = _scrape_paginated(company)
        else:
            html = fetch_html_with_retry(company)
            jobs = extract_jobs(html, company)
        filters = company.get("filters")
        if filters:
            jobs = [j for j in jobs if _matches_filters(j, filters)]
        return jobs, None
    except ScrapeError as e:
        logger.error("Scrape-Fehler bei %s: %s", company.get("name"), e)
        return [], str(e)
    except requests.RequestException as e:
        logger.error("Netzwerkfehler bei %s: %s", company.get("name"), e)
        return [], f"Seite nicht erreichbar: {e}"
    except Exception as e:  # noqa: BLE001 - bewusst breit, ein Firmenfehler darf den Lauf nicht stoppen
        logger.exception("Unerwarteter Fehler bei %s", company.get("name"))
        return [], f"Unerwarteter Fehler: {e}"
