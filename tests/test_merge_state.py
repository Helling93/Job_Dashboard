import storage


def job(**kw):
    base = {"title": "T", "link": "https://x/1", "status": "neu", "first_seen": "2026-10-01",
            "last_seen": "2026-10-20", "disappeared_at": None, "location": "Munich"}
    return {**base, **kw}


def state(jobs, last_run="2026-10-20T08:00:00+00:00"):
    return {"last_run": last_run, "companies": {"Firma": {"jobs": jobs}}}


def test_user_status_click_during_run_survives_and_scraper_changes_are_applied():
    base = state({"a": job(), "b": job(link="https://x/2")})
    # Scraper-Lauf: last_seen aktualisiert, neuer Job, missed_runs entfernt, last_run neu
    ours = state({"a": job(last_seen="2026-10-21"),
                  "b": job(link="https://x/2", last_seen="2026-10-21"),
                  "n": job(link="https://x/3", first_seen="2026-10-21")}, last_run="2026-10-21T08:00:00+00:00")
    # Dashboard-Klick währenddessen: Job a -> interessant
    theirs = state({"a": job(status="interessant"), "b": job(link="https://x/2")})

    merged = storage.merge_states(base, ours, theirs)
    jobs = merged["companies"]["Firma"]["jobs"]
    assert jobs["a"]["status"] == "interessant"          # Nutzer gewinnt
    assert jobs["a"]["last_seen"] == "2026-10-21"        # Scraper-Änderung trotzdem drin
    assert jobs["b"]["last_seen"] == "2026-10-21"
    assert "n" in jobs and jobs["n"]["status"] == "neu"  # neuer Job kommt dazu
    assert merged["last_run"] == "2026-10-21T08:00:00+00:00"


def test_scraper_auto_hide_applies_unless_user_changed_status_too():
    base = state({"a": job(), "b": job(link="https://x/2")})
    ours = state({"a": job(status="kein interesse", auto_hidden="ausbildung", requirement="vocational"),
                  "b": job(link="https://x/2", status="kein interesse", auto_hidden="ausbildung")})
    theirs = state({"a": job(), "b": job(link="https://x/2", status="interessant")})  # Nutzer fasst b an

    jobs = storage.merge_states(base, ours, theirs)["companies"]["Firma"]["jobs"]
    assert jobs["a"]["status"] == "kein interesse" and jobs["a"]["auto_hidden"] == "ausbildung"
    assert jobs["b"]["status"] == "interessant"          # Nutzerwahl schlägt Auto-Ausblenden


def test_removed_fields_and_foreign_jobs_are_handled():
    base = state({"a": job(missed_runs=1), "gone": job(link="https://x/9")})
    ours = state({"a": job()})                           # missed_runs vom Scraper entfernt
    ours["companies"]["Firma"]["jobs"]["gone"] = job(link="https://x/9", last_seen="2026-10-21")
    theirs = state({"a": job(missed_runs=1), "other": job(link="https://x/7")})  # "gone" extern entfernt, "other" extern neu

    jobs = storage.merge_states(base, ours, theirs)["companies"]["Firma"]["jobs"]
    assert "missed_runs" not in jobs["a"]
    assert "gone" not in jobs                            # nicht wiederbeleben
    assert "other" in jobs                               # fremde Änderung bleibt


def test_merge_without_concurrent_changes_equals_ours():
    base = state({"a": job()})
    ours = state({"a": job(last_seen="2026-10-21", countries=["DE"]), "n": job(link="https://x/3")},
                 last_run="2026-10-21T08:00:00+00:00")
    assert storage.merge_states(base, ours, base) == ours
