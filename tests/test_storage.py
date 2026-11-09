from scraper import Job
from storage import update_company


def _job(n):
    return Job(title=f"Job {n}", link=f"https://example.com/{n}")


def test_job_disappears_only_after_two_missed_runs():
    state = {}
    update_company(state, "Firma", [_job(1), _job(2)], "2026-10-01")

    # Ein Lauf ohne Job 2 (z.B. bei Pagination übersehen) - noch nicht verschwunden
    _, gone = update_company(state, "Firma", [_job(1)], "2026-10-02")
    assert gone == []
    rec = next(r for r in state["companies"]["Firma"]["jobs"].values() if r["title"] == "Job 2")
    assert rec["disappeared_at"] is None

    # Taucht wieder auf -> Zähler zurückgesetzt, kein "neuer" Job
    new, gone = update_company(state, "Firma", [_job(1), _job(2)], "2026-10-03")
    assert new == [] and gone == []
    assert "missed_runs" not in rec

    # Zwei Läufe in Folge fehlend -> verschwunden
    update_company(state, "Firma", [_job(1)], "2026-10-04")
    _, gone = update_company(state, "Firma", [_job(1)], "2026-10-05")
    assert [g["title"] for g in gone] == ["Job 2"]
    assert rec["disappeared_at"] == "2026-10-05"


def test_successful_scrape_records_last_success():
    state = {}
    update_company(state, "Firma", [_job(1)], "2026-10-01")
    assert state["companies"]["Firma"]["last_success"] == "2026-10-01"


def test_jobs_not_seen_for_days_expire_even_without_successful_scrape():
    from storage import expire_stale_jobs

    state = {}
    update_company(state, "Kaputt", [_job(1)], "2026-10-01")   # danach scheitert jeder Lauf
    update_company(state, "Ok", [_job(2)], "2026-10-08")
    rec = next(iter(state["companies"]["Kaputt"]["jobs"].values()))
    rec["status"] = "beworben"

    assert expire_stale_jobs(state, "2026-10-08", 7) == []     # genau 7 Tage: noch offen
    gone = expire_stale_jobs(state, "2026-10-09", 7)
    assert [g["company"] for g in gone] == ["Kaputt"]
    assert rec["disappeared_at"] == "2026-10-09"
    assert rec["status"] == "beworben"                          # Status bleibt erhalten

    # Firma wird wieder erfolgreich gescraped und die Stelle gibt es noch -> wieder offen
    new, _ = update_company(state, "Kaputt", [_job(1)], "2026-10-10")
    assert new == [] and rec["disappeared_at"] is None
