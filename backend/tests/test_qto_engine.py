"""Tests for the ported QTO zone-detection engine.

Parity target = the lead's `outdoor_qto.py` output on L1.01 of the Kirby
LANDSCAPE PDF (scale 1/16" = 1'-0"): M.5=6402, M.6=4936, M.7=190.
The QTO reference is M.5=6550, M.7=191 (both within 10%); M.6's reference is
1760 — out of tolerance in BOTH the lead's script and this port (a known
algorithm limitation, tracked separately, not a port defect).
"""
import re
from pathlib import Path

import numpy as np

from backend import qto_engine

PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"
L1_01_PAGE = 2  # 0-based
TAG_RE = re.compile(r"^\(?M[-.]?(\d{1,2})\)?$", re.I)
CLIP = {"top": 0.05, "bottom": 0.92, "left": 0.0, "right": 0.80}


def test_thick_boundaries_are_sparser_than_full_ink():
    thick = qto_engine.render_thick_boundaries(PDF, L1_01_PAGE, dpi=120)
    black_frac = float((thick < 128).mean())
    assert 0.0005 < black_frac < 0.05, black_frac


def test_preprocess_returns_binary_fillable():
    thick = qto_engine.render_thick_boundaries(PDF, L1_01_PAGE, dpi=120)
    binary = qto_engine.preprocess_for_fill(thick)
    assert set(np.unique(binary).tolist()) <= {0, 255}
    assert (binary == 255).mean() > 0.5


def test_extract_M_tags_on_L1_01():
    tags = qto_engine.extract_tags(PDF, L1_01_PAGE, CLIP, TAG_RE, numeric_only=True)
    codes = {t["code"] for t in tags}
    assert "M.5" in codes and "M.6" in codes


def test_px_to_sqft_scale_math():
    ft_per_px = (1 / 150) / (1 / 16)
    assert abs(qto_engine.px_to_sqft(1, 150, 1 / 16) - ft_per_px ** 2) < 1e-9


def _l1_01_areas():
    cfg = {
        "scale_in_per_ft": 1 / 16,
        "tag_pattern": r"^\(?M[-.]?(\d{1,2})\)?$",
        "tag_numeric_only": True,
        "clip": CLIP,
        "phase1_min_zone_sf": 0,
        "phase2_radius_ft": 24,
    }
    return qto_engine.run_sheet(PDF, L1_01_PAGE, cfg, Path("outputs/_test_L1_01.png"), dpi=150)


def test_run_sheet_reproduces_lead_numbers():
    res = _l1_01_areas()
    a = res["areas"]
    # parity with the lead's script (within 3% for render/rounding jitter)
    for code, expected in {"M.5": 6402, "M.6": 4936, "M.7": 190}.items():
        assert abs(a[code] - expected) / expected < 0.03, (code, a.get(code))
    assert Path(res["overlay"]).name == "_test_L1_01.png"


def test_run_sheet_M5_M7_within_10pct_of_QTO():
    a = _l1_01_areas()["areas"]
    for code, ref in {"M.5": 6550, "M.7": 191}.items():
        assert abs(a[code] - ref) / ref < 0.10, (code, a.get(code))
