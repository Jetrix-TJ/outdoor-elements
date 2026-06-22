"""Tests for zone_filter wiring into _persist_masks."""
from unittest.mock import MagicMock
import pytest


def test_filter_called_in_persist_masks(monkeypatch, tmp_path):
    """filter_false_positives is called when _persist_masks runs."""
    import numpy as np
    from backend import tasks, store, zones as zones_mod, zone_filter

    job_id = "filterwire01"
    page = 0

    # Stub store paths to tmp_path
    monkeypatch.setattr(store, "JOBS_DIR", tmp_path)
    (tmp_path / job_id).mkdir()
    (tmp_path / job_id / "stage2").mkdir()
    # Create a fake PDF path (won't be opened since filter is mocked)
    fake_pdf = tmp_path / job_id / "upload.pdf"
    fake_pdf.write_bytes(b"")

    # Stub extract_zones_from_label to return 2 fake zones
    fake_zones = [
        {"id": "z1", "code": "M.5", "hex": "#fff", "area_sqft": 200.0,
         "perimeter_lf": 50.0, "geometry": [], "bbox": [0.1, 0.1, 0.4, 0.4],
         "source": "engine", "status": "active"},
        {"id": "z2", "code": "M.5", "hex": "#fff", "area_sqft": 2.0,
         "perimeter_lf": 5.0, "geometry": [], "bbox": [0.8, 0.1, 0.9, 0.2],
         "source": "engine", "status": "active"},
    ]
    monkeypatch.setattr(zones_mod, "extract_zones_from_label", lambda *a, **kw: fake_zones)

    # Stub store.replace_zones to capture what was passed
    captured = []
    monkeypatch.setattr(store, "replace_zones", lambda jid, pg, zl: captured.append(zl))
    monkeypatch.setattr(store, "masks_path", lambda *a: tmp_path / "masks.npz")

    # Stub zone_filter to record call and return only z1 (simulating drop of z2)
    filter_called = []
    def fake_filter(zlist, *args, **kwargs):
        filter_called.append(zlist)
        return [z for z in zlist if z["area_sqft"] > 10]
    monkeypatch.setattr(zone_filter, "filter_false_positives", fake_filter)

    # Build a simple 1-code mask
    mask = np.ones((10, 10), dtype=np.uint8) * 255
    tasks._persist_masks(job_id, page, {"M.5": mask}, scale=1/16, dpi=150)

    assert len(filter_called) == 1, "filter_false_positives was not called"
    assert len(captured[0]) == 1, "only the large zone should be stored"
    assert captured[0][0]["id"] == "z1"

    import shutil
    shutil.rmtree(tmp_path / job_id, ignore_errors=True)
