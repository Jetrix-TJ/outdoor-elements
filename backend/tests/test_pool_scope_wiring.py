"""Test that run_stage1_config writes pool_scope into config."""
import os
import shutil
import tempfile

import fitz

from backend import db, store
from backend.tasks import run_stage1_config


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()


SCOPE_TEXT = """\
SWIMMING POOL (1,109 SF)
1. Shop drawings and coordination.
Swimming Pool Total: $549,863.52
"""


def _make_pdf(text: str) -> str:
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), text)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    return tmp


def test_run_stage1_config_writes_pool_scope(monkeypatch):
    _fresh_db()
    job_id = "testjob01"
    # set up job dir and fake PDFs
    jd = store.job_dir(job_id)
    jd.mkdir(parents=True, exist_ok=True)

    # main PDF (one blank page — config build will fail gracefully via monkeypatch)
    _blank = fitz.open()
    _blank.new_page()
    _blank.save(str(store.pdf_path(job_id)))
    _blank.close()

    # estimate PDF with pool scope
    est_pdf = _make_pdf(SCOPE_TEXT)
    shutil.copy(est_pdf, str(store.estimate_path(job_id)))
    os.unlink(est_pdf)

    store.write_status(job_id, {"job_id": job_id, "status": "queued"})

    # stub out Gemini so test doesn't need network
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr("backend.pool_scope._classify_with_gemini",
                        lambda items, api_key: items)
    monkeypatch.setattr("backend.gemini_config.build_config",
                        lambda *a, **kw: {"sheets": {}, "materials": {}, "source": "fallback"})

    run_stage1_config(job_id, "test.pdf")

    cfg = store.read_config(job_id)
    assert cfg is not None
    assert "pool_scope" in cfg
    scope = cfg["pool_scope"]
    assert scope["scope_type"] == "SWIMMING POOL"
    assert scope["area_sf"] == 1109
    assert scope["total_price"] == 549863.52
    assert len(scope["items"]) >= 1

    # Clean up test job directory
    shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
