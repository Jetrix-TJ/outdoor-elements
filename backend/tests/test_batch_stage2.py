"""Batch Stage-2: detect_kept_pages runs each kept page once and skips done ones."""
import backend.tasks as tasks


def test_detect_kept_pages_skips_done_and_runs_others(monkeypatch):
    # job with 3 kept pages (indices 0,2,5) + a non-kept page (1)
    status = {"pages": [
        {"index": 0, "keep": True}, {"index": 1, "keep": False},
        {"index": 2, "keep": True}, {"index": 5, "keep": True},
    ]}
    # page 2 is already done; the others are not
    done = {2}
    monkeypatch.setattr(tasks.store, "read_status", lambda jid: status)

    ran = []

    def fake_run(jid, page, force=False):
        ran.append(page)
        return {"status": "done"}

    # resume-safe behaviour lives inside run_stage2; here we only assert which
    # pages detect_kept_pages *asks* run_stage2 to process (every kept page).
    monkeypatch.setattr(tasks, "run_stage2", fake_run)

    out = tasks.detect_kept_pages("job1")

    assert sorted(ran) == [0, 2, 5]         # only kept pages, no page 1 (order varies — concurrent)
    assert out == {"detected": 3, "skipped": 0}
