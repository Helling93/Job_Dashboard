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
