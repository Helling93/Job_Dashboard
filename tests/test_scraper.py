from pathlib import Path

from scraper import ScrapeError, extract_jobs

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
