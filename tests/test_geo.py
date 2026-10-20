from pathlib import Path

import yaml

import dashboard
import geo

PLACES = {
    "DE": ["München", "Munich", "Gilching", "Manching"],
    "CH": ["Zürich", "Zurich", "Winterthur", "Hinwil"],
    "LI": ["Eschen"],
}


def test_detect_countries_from_real_world_location_strings():
    d = lambda loc, fb=None: geo.detect_countries(loc, PLACES, fb)  # noqa: E731

    assert d("Munich,Bayern,Germany") == ["DE"]
    assert d("Hinwil Switzerland | Full time") == ["CH"]
    assert d("Zürich,Zurich,SwitzerlandPayerne,Vaud,Switzerland") == ["CH"]
    assert d("Hengelo,Overijssel,NetherlandsZürich,Zurich,Switzerland") == ["CH"]
    assert d("Rheinmetall Air Defence AG | Zürich") == ["CH"]
    assert d("Eschen, Liechtenstein") == ["LI"]
    # mehrere Länder, feste Reihenfolge CH, DE
    assert d("Munich, Germany or Zurich, Switzerland") == ["CH", "DE"]
    # Orte außerhalb der Zielregion werden ignoriert
    assert d("Berlin; London; Munich") == ["DE"]
    assert d("Berlin; London") == []
    # Akzente egal
    assert d("ZURICH") == ["CH"] and d("Zuerich") == [] and d("Muenchen") == []


def test_detect_countries_company_fallback_only_when_nothing_found():
    assert geo.detect_countries("", PLACES, "DE") == ["DE"]
    assert geo.detect_countries(None, PLACES, "DE") == ["DE"]
    assert geo.detect_countries("2 Standorte", PLACES, "DE") == ["DE"]
    assert geo.detect_countries("Zürich", PLACES, "DE") == ["CH"]  # Ort schlägt Fallback
    assert geo.detect_countries("", PLACES) == []


def test_assign_countries_covers_all_jobs_and_is_idempotent():
    state = {"companies": {
        "A": {"jobs": {"1": {"location": "Munich"}, "2": {"location": "Zurich", "disappeared_at": "2026-10-01"}}},
        "B": {"jobs": {"3": {"location": ""}}},
    }}
    companies = [{"name": "A"}, {"name": "B", "country": "DE"}]
    assert geo.assign_countries(state, companies, PLACES) == 3
    jobs = {**state["companies"]["A"]["jobs"], **state["companies"]["B"]["jobs"]}
    assert jobs["1"]["countries"] == ["DE"]
    assert jobs["2"]["countries"] == ["CH"]  # auch verschwundene Jobs
    assert jobs["3"]["countries"] == ["DE"]  # Firmen-Fallback
    assert geo.assign_countries(state, companies, PLACES) == 0


def test_config_country_places_cover_every_region_place():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
    settings = cfg["settings"]
    covered = {geo._fold(p) for places in settings["country_places"].values() for p in places}
    places = list(settings["global_region_contains"])
    for company in cfg["companies"]:  # firmenspezifische Ergänzungen (z.B. Zug bei Siemens)
        places += (company.get("filters") or {}).get("extra_region_contains") or []
    missing = [p for p in places if p != "Standorte" and geo._fold(p) not in covered]
    assert not missing, f"Orte in global_region_contains/extra_region_contains ohne Land in country_places: {missing}"


def test_dashboard_renders_country_badge_attr_and_filter_chips():
    def job(title, countries):
        rec = {"title": title, "link": f"https://x/{title}", "status": "neu",
               "first_seen": "2026-10-20", "disappeared_at": None, "location": "x"}
        if countries is not None:
            rec["countries"] = countries
        return rec

    state = {"last_run": None, "companies": {"Firma": {"jobs": {
        "a": job("Swiss Job", ["CH"]),
        "b": job("German Job", ["DE"]),
        "c": job("Both Job", ["CH", "DE"]),
        "d": job("No Country Job", None),
    }}}}
    html = dashboard.render_dashboard(state, {}, 0)

    assert 'data-countries="CH"' in html and 'data-countries="DE"' in html
    assert 'data-countries="CH DE"' in html
    assert 'data-countries="unbekannt"' in html
    assert '<span class="country-pill cp-ch">Schweiz</span>' in html
    assert '<span class="country-pill cp-de">Deutschland</span>' in html
    for code in ("CH", "DE", "unbekannt"):
        assert f'data-country="{code}"' in html  # Filter-Chips
    assert 'data-country="LI"' not in html  # kein Chip für Länder ohne Jobs
