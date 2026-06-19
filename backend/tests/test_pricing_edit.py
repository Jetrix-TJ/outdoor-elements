"""Pricing computation + manual click-to-remove correction."""
import shutil
import tempfile
from pathlib import Path

import fitz
import numpy as np

from backend import pricing, store, tasks

LAND = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"


def test_price_takeoff_totals_and_sort():
    t = pricing.price_takeoff({"M.5": 6550, "M.6": 1760}, {"M.5": 18.0, "M.6": 18.0})
    assert t["total"] == round(6550 * 18 + 1760 * 18, 2)
    assert t["rows"][0]["code"] == "M.5"        # sorted by qty desc
    assert t["rows"][0]["cost"] == round(6550 * 18, 2)


def test_default_rate_by_family():
    assert pricing.default_rate("M.15", "RIVER ROCK") == 9.0
    assert pricing.default_rate("M.12", "ARTIFICIAL TURF A") == 14.0
    assert pricing.default_rate("M.99", "") == 15.0


def test_remove_region_drops_clicked_zone(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(store, "JOBS_DIR", tmp)
    job = store.new_job_id()
    shutil.copy(LAND, store.pdf_path(job))
    page = 2
    doc = fitz.open(store.pdf_path(job))
    pix = doc[page].get_pixmap(dpi=150)
    doc.close()
    H, W = pix.height, pix.width

    masks = {"M.5": np.zeros((H, W), np.uint8), "M.6": np.zeros((H, W), np.uint8)}
    masks["M.5"][1000:1200, 1000:1200] = 255
    masks["M.6"][2000:2200, 2000:2200] = 255
    tasks._persist_masks(job, page, masks, scale=1 / 16)
    store.write_stage2(job, page, {"job_id": job, "page": page, "status": "done",
                                   "sheet": "L1.01",
                                   "groups": [{"label": "M.5", "sqft": 1.0, "regions": 1,
                                               "hex": "#fff"}]})

    out = tasks.remove_region(job, page, 1100 / W, 1100 / H)   # click inside M.5
    areas = {g["label"]: g["sqft"] for g in out["groups"]}
    assert "M.5" not in areas          # the only M.5 block was removed
    assert "M.6" in areas              # M.6 untouched
    assert (store.stage2_dir(job) / f"overlay_p{page}.png").exists()
    assert "Removed" in out["edit_note"]
