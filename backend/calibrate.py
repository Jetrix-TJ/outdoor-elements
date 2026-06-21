"""Calibration harness — grade our raw->QTO detector against the human QTO.

This is the development feedback loop for the "raw plan in, QTO out" goal:
run detection on a RAW plan page, parse the human QTO ground truth (the answer
key), and report a per-item AI-vs-human comparison with accuracy (MAPE on area).
The QTO is used ONLY here for grading — never at runtime.

    from backend import calibrate
    rep = calibrate.grade_area(RAW_PDF, page=2, qto_pdf=QTO_PDF)
    calibrate.print_report(rep)
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz

from . import groundtruth, qto_engine, store


@dataclass
class Row:
    code: str
    human: float | None
    ours: float | None
    err_pct: float | None


def _auto_cfg(raw_pdf: str, page: int) -> dict:
    """Build a per-sheet engine config for a raw material plan — the material tag
    family is auto-detected from the sheet (Kirby M.x / Pelican A#), so detection
    is restricted to real materials (no plant/wall/note over-capture)."""
    from . import legend, stage2
    doc = fitz.open(raw_pdf)
    fpi = stage2.sheet_feet_per_inch(doc[page], default=16.0)
    doc.close()
    clip = {"top": 0.05, "bottom": 0.92, "left": 0.0, "right": 0.80}
    tcfg = legend.tag_config(raw_pdf, page, clip)
    pattern = tcfg["tag_pattern"] if tcfg else r"^\(?(M[-.]?\d{1,2})\)?$"
    return {
        "sheet_id": "CAL", "title": "Material Plan",
        "scale_in_per_ft": 1.0 / fpi,
        "tag_pattern": pattern, "tag_numeric_only": False,
        "clip": clip, "phase1_min_zone_sf": 0, "phase2_radius_ft": 24,
    }


def detect_area(raw_pdf: str, page: int, cfg: dict | None = None) -> dict:
    """Run the line-width zone engine on a raw page -> {code: sqft}."""
    cfg = cfg or _auto_cfg(raw_pdf, page)
    out = store.STORE_ROOT / "_cal_overlay.png" if hasattr(store, "STORE_ROOT") else None
    res = qto_engine.run_sheet(raw_pdf, page, cfg, out or "_cal_overlay.png")
    return {k: float(v) for k, v in (res.get("areas") or {}).items()}


def grade_area(raw_pdf: str, page: int, qto_pdf: str, cfg: dict | None = None) -> dict:
    """Compare detected area-by-code against the human QTO area items."""
    ours = detect_area(raw_pdf, page, cfg)
    gt = groundtruth.parse_qto(qto_pdf)
    # human area by code (sum repeats of the same code across the sheet)
    human: dict[str, float] = {}
    for it in gt:
        if it.unit == "sqft" and it.code:
            human[it.code] = human.get(it.code, 0.0) + it.qty

    codes = sorted(set(human) | set(ours))
    rows: list[Row] = []
    for c in codes:
        h, o = human.get(c), ours.get(c)
        err = (abs(o - h) / h * 100) if (h and o is not None and h) else None
        rows.append(Row(c, h, o, round(err, 1) if err is not None else None))

    matched = [r for r in rows if r.err_pct is not None]
    mape = round(sum(r.err_pct for r in matched) / len(matched), 1) if matched else None
    return {"rows": rows, "mape": mape, "matched": len(matched),
            "human_only": [r.code for r in rows if r.ours is None and r.human],
            "ours_only": [r.code for r in rows if r.human is None and r.ours]}


def print_report(rep: dict) -> None:
    print(f"\n  {'CODE':8} {'HUMAN':>12} {'OURS':>12} {'ERR%':>8}")
    print("  " + "-" * 42)
    for r in rep["rows"]:
        h = f"{r.human:,.1f}" if r.human is not None else "—"
        o = f"{r.ours:,.1f}" if r.ours is not None else "—"
        e = f"{r.err_pct}" if r.err_pct is not None else "—"
        print(f"  {r.code:8} {h:>12} {o:>12} {e:>8}")
    print("  " + "-" * 42)
    print(f"  MAPE={rep['mape']}%  on {rep['matched']} matched codes")
    if rep["human_only"]:
        print(f"  MISSED (human has, we don't): {rep['human_only']}")
    if rep["ours_only"]:
        print(f"  EXTRA  (we have, human doesn't): {rep['ours_only']}")
