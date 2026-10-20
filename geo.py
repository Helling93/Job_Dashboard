"""Ordnet Stellen anhand ihres Standort-Texts einem Land zu (Schweiz/Deutschland/...).

Die Standort-Strings sind pro Firma völlig uneinheitlich ("Munich,Bayern,Germany",
"Zürich,Zurich,SwitzerlandPayerne,Vaud,Switzerland", "Hinwil Switzerland | Full
time", "Berlin; London; Munich", manchmal leer). Erkannt wird deshalb per
Teilstring:
  * Ortsnamen aus `settings.country_places` (Zielregionen München/Zürich), und
  * Länderwörter (Switzerland, Schweiz, Germany, Bayern, ...).
Ein Job kann mehrere Länder haben ("Zürich ... or Munich ..."). Orte außerhalb der
Zielregionen (London, Paris, ...) werden bewusst ignoriert - relevant ist, wo man
sich praktisch hin bewerben würde. Findet sich nichts, greift der Firmen-Fallback
`country:` aus der config.yaml (z.B. bei leerem Standort).
"""

from __future__ import annotations

import unicodedata

COUNTRY_LABELS = {"CH": "Schweiz", "DE": "Deutschland", "LI": "Liechtenstein"}
COUNTRY_ORDER = ("CH", "DE", "LI")

COUNTRY_KEYWORDS = {
    "CH": ("switzerland", "schweiz", "suisse", "svizzera"),
    "DE": ("germany", "deutschland", "bayern", "bavaria"),
    "LI": ("liechtenstein",),
}


def _fold(text: str) -> str:
    """Klein, ohne Akzente/Umlaute-Striche ("Zürich" -> "zurich", "ß" -> "ss")."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("ß", "ss")


def _as_list(value) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def detect_countries(
    location: str | None,
    country_places: dict[str, list[str]],
    fallback=None,
) -> list[str]:
    """Länder-Codes (sortiert CH, DE, LI) für einen Standort-Text; [] = unbekannt."""
    found: set[str] = set()
    folded = _fold(location or "")
    if folded:
        for code in COUNTRY_ORDER:
            words = list(COUNTRY_KEYWORDS.get(code, ()))
            words += [_fold(p) for p in _as_list(country_places.get(code))]
            if any(w and w in folded for w in words):
                found.add(code)
    if not found:
        found = {c for c in _as_list(fallback) if c}
    return [c for c in COUNTRY_ORDER if c in found]


def assign_countries(state: dict, companies: list[dict], country_places: dict[str, list[str]]) -> int:
    """Setzt `countries` an JEDEM Job im State (auch an verschwundenen) neu.

    Wird bei jedem Lauf für alle Einträge berechnet - dadurch greifen geänderte
    Ortslisten/Firmen-Fallbacks automatisch auch für schon bekannte Stellen.
    Gibt die Anzahl geänderter Einträge zurück."""
    by_name = {c.get("name"): c for c in companies}
    changed = 0
    for cname, cdata in state.get("companies", {}).items():
        fallback = (by_name.get(cname) or {}).get("country")
        for rec in cdata.get("jobs", {}).values():
            countries = detect_countries(rec.get("location"), country_places, fallback)
            if rec.get("countries") != countries:
                rec["countries"] = countries
                changed += 1
    return changed
