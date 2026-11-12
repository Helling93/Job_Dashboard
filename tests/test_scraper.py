from pathlib import Path

from scraper import Job, ScrapeError, _matches_filters, extract_jobs

FIXTURE = (Path(__file__).parent / "fixtures" / "sample_company.html").read_text(encoding="utf-8")

COMPANY_CFG = {
    "name": "Sample Company",
    "url": "https://sample.example/careers",
    "base_url": "https://sample.example",
    "list_selector": "li.job-listing",
    "title_selector": "h2.job-title",
    "link_selector": "a",
    "link_attr": "href",
    "location_selector": ".job-location",
    "date_selector": ".job-date",
}


def test_extract_jobs_parses_all_fields():
    jobs = extract_jobs(FIXTURE, COMPANY_CFG)

    assert len(jobs) == 2

    first = jobs[0]
    assert first.title == "Konstrukteur (m/w/d) Fahrwerk"
    assert first.link == "https://sample.example/jobs/1234"
    assert first.location == "Zürich"
    assert first.date_posted == "vor 2 Tagen"

    second = jobs[1]
    assert second.title == "AI Engineer Autonomous Driving"
    assert second.link == "https://sample.example/jobs/5678"


def test_extract_jobs_raises_when_selector_matches_nothing():
    bad_cfg = {**COMPANY_CFG, "list_selector": "li.does-not-exist"}
    try:
        extract_jobs(FIXTURE, bad_cfg)
        assert False, "sollte ScrapeError werfen"
    except ScrapeError:
        pass


def test_region_filter_keeps_jobs_without_location_and_multi_site_matches():
    filters = {"region_contains": ["Munich", "Zürich"]}

    def job(location):
        return Job(title="Systems Engineer", link="https://x", location=location)

    assert _matches_filters(job("Berlin; London; Munich"), filters)
    assert _matches_filters(job(None), filters)
    assert _matches_filters(job(""), filters)
    assert not _matches_filters(job("London"), filters)


def test_all_config_selectors_compile_with_installed_soupsieve():
    # Actions installiert soupsieve ungepinnt - neuere Versionen sind strenger
    # (z.B. 3.0: keine komplexen Kombinatoren in :has()). Fängt solche Brüche
    # lokal ab, statt erst als Telegram-Fehlermeldung nach dem Cron-Lauf.
    import soupsieve
    import yaml

    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
    keys = ("list_selector", "title_selector", "link_selector",
            "location_selector", "date_selector", "category_selector")
    for company in cfg["companies"]:
        for key in keys:
            selector = company.get(key)
            if selector and selector not in ("self", ":self"):
                soupsieve.compile(selector)  # wirft bei ungültigem/zu strengem Selektor


# --- Anforderungs-Check (qualifications.py) ---------------------------------

def test_classify_degree_wins_over_vocational():
    import qualifications as q

    text = "Abgeschlossene Ausbildung als Elektroniker oder ein abgeschlossenes Studium der Elektrotechnik"
    assert q.classify_requirements(text)[0] == "degree"


def test_classify_vocational_only():
    import qualifications as q

    verdict, evidence = q.classify_requirements(
        "Ihr Profil: Abgeschlossene Berufsausbildung als Mechatroniker, Erfahrung in der Montage."
    )
    assert verdict == "vocational"
    assert "Berufsausbildung" in evidence


def test_classify_unknown_and_no_false_degree_on_master_data():
    import qualifications as q

    # "Master Data" ist kein Studienabschluss; ohne weitere Hinweise -> unklar (bleibt sichtbar)
    assert q.classify_requirements("Pflege der Master Data im ERP, Teamarbeit")[0] == "unknown"
    # Benefits-Wort "Ausbildung" allein ist keine Anforderung
    assert q.classify_requirements("Wir bieten Fort- und Ausbildung sowie 30 Tage Urlaub")[0] == "unknown"


def test_title_implies_degree():
    import qualifications as q

    assert q.title_implies_degree("Senior Systems Engineer (w/m/d)")
    assert q.title_implies_degree("Projektleiter Mittelkaliber")
    assert not q.title_implies_degree("Konstrukteur:in")
    assert not q.title_implies_degree("Baugruppenmonteur Vormontage (m/w/d)")


def test_apply_requirement_filter_hides_only_vocational_new_jobs(monkeypatch):
    import qualifications as q

    def rec(title, status="neu", **kw):
        return {"title": title, "link": f"https://x/{title}", "status": status,
                "first_seen": "2026-10-20", "disappeared_at": None, **kw}

    state = {"companies": {"Firma": {"jobs": {
        "a": rec("Mechatroniker:in"),                     # -> vocational, wird ausgeblendet
        "b": rec("Systems Engineer"),                     # Titel-Schutz, kein Abruf
        "c": rec("Sachbearbeiter"),                       # -> degree, bleibt
        "d": rec("Monteur", status="interessant"),        # nicht "neu" -> nie anfassen
        "e": rec("Techniker", requirement="degree"),      # schon geprüft -> nicht erneut
        "f": rec("Unlesbar"),                             # Abruf scheitert
    }}}}
    texts = {
        "a": "x " * 300 + "Abgeschlossene Ausbildung als Mechatroniker:in",
        "c": "x " * 300 + "Studium oder Ausbildung",
        "f": None,
    }
    fetched = []

    def fake_fetch(company, items, deadline):
        fetched.extend(jid for jid, _ in items)
        return {jid: texts[jid] for jid, _ in items}

    monkeypatch.setattr(q, "fetch_detail_texts", fake_fetch)
    hidden = q.apply_requirement_filter(state, [{"name": "Firma"}], {"max_checks_per_run": 10})

    jobs = state["companies"]["Firma"]["jobs"]
    assert hidden == {"a"}
    assert jobs["a"]["status"] == "kein interesse" and jobs["a"]["auto_hidden"] == "ausbildung"
    assert jobs["b"]["requirement"] == "degree" and "b" not in fetched
    assert jobs["c"]["requirement"] == "degree" and jobs["c"]["status"] == "neu"
    assert jobs["d"]["status"] == "interessant" and "requirement" not in jobs["d"]
    assert "e" not in fetched
    assert jobs["f"]["requirement_attempts"] == 1 and "requirement" not in jobs["f"]
    # zweiter Fehlversuch -> "unreadable", Job bleibt sichtbar
    q.apply_requirement_filter(state, [{"name": "Firma"}], {"max_checks_per_run": 10})
    assert jobs["f"]["requirement"] == "unreadable" and jobs["f"]["status"] == "neu"


def test_classify_business_degree_only():
    import qualifications as q

    verdict, evidence = q.classify_requirements(
        "What you bring: Bachelor's or Master's degree in Finance, Accounting, Controlling, "
        "Economics, or a related field. 3+ years of experience in treasury."
    )
    assert verdict == "business"
    assert "Finance" in evidence
    assert q.classify_requirements("Abgeschlossenes BWL-Studium mit Schwerpunkt Controlling")[0] == "business"


def test_classify_business_degree_with_technical_alternative_stays():
    import qualifications as q

    # Sobald irgendeine Studium-Passage eine technische Richtung nennt, bleibt die Stelle
    assert q.classify_requirements(
        "Master's degree in Engineering, Business Administration or a comparable field"
    )[0] == "degree"
    assert q.classify_requirements(
        "Studium der Wirtschaftswissenschaften oder Wirtschaftsingenieurwesen"
    )[0] == "degree"
    assert q.classify_requirements(
        "Degree in Finance. Alternatively a Bachelor in Aerospace with interest in budgeting."
    )[0] == "degree"
    # "Steuerungstechnik" ist kein Steuerrecht
    assert q.classify_requirements("Studium mit Vertiefung Steuerungstechnik")[0] == "degree"


def test_title_is_technical():
    import qualifications as q

    assert q.title_is_technical("Senior Systems Engineer (w/m/d)")
    assert not q.title_is_technical("Junior Treasury Manager (w/m/d)")
    assert not q.title_is_technical("Program Manager")


def test_vocational_with_open_alternative_stays_visible():
    import qualifications as q

    # Flux: Ausbildung ODER vergleichbare Qualifikation -> auch Studierte gemeint
    assert q.classify_requirements(
        "Abgeschlossene Ausbildung als Konstrukteur:in mit eidg. Fähigkeitszeugnis oder eine "
        "vergleichbare Qualifikation. 3 bis 5 Jahre Berufserfahrung in der Konstruktion."
    )[0] == "unknown"
    # "vergleichbare Ausbildung" öffnet nichts
    assert q.classify_requirements(
        "Abgeschlossene Berufsausbildung als Mechatroniker oder vergleichbare Ausbildung"
    )[0] == "vocational"


def test_konstrukteur_title_is_never_hidden_as_vocational(monkeypatch):
    import qualifications as q

    state = {"companies": {"Firma": {"jobs": {"k": {
        "title": "Konstrukteur:in", "link": "https://x/k", "status": "neu",
        "first_seen": "2026-10-20", "disappeared_at": None}}}}}
    monkeypatch.setattr(q, "fetch_detail_texts", lambda c, items, d: {
        "k": "x " * 300 + "Abgeschlossene Ausbildung als Konstrukteur:in EFZ"})
    assert q.apply_requirement_filter(state, [{"name": "Firma"}], {}) == set()
    job = state["companies"]["Firma"]["jobs"]["k"]
    assert job["status"] == "neu" and job["requirement"] == "unknown"
    assert not q.title_never_vocational("Konstruktionsmechaniker (m/w/d)")
