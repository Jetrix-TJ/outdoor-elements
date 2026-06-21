"""raw -> QTO orchestrator.

Ties the pieces together: read the per-material plan (the 'brain' — what each
material is and how to measure it), then detect each material by its method and
assemble a COMPLETE QTO output — area (sq ft), linear (ft) AND count (each), so
walls and counts are no longer missing.

    closed_area / open_hatch -> line-width zone engine (sq ft)
    line                     -> sum of vector line length near the code's tags (ft)
    symbol                   -> number of the code's callout tags (each)

    from backend import takeoff
    items = takeoff.build_takeoff(raw_pdf, page)   # uses GEMINI_API_KEY if available
    # -> [{code, name, unit, quantity, detect}], one per material
"""
from __future__ import annotations

import collections
import math
import re

import fitz

from . import calibrate, legend, material_plan

_CLIP = {"top": 0.05, "bottom": 0.92, "left": 0.0, "right": 0.80}
_TAG = re.compile(r"^\(?([A-Za-z]{1,3}[-.]?\d{1,2})\)?$")


def _tag_positions(pdf: str, page: int, clip: dict) -> dict[str, list[tuple]]:
    """Deduped callout tag positions (PDF points) per FULL code on the plan."""
    doc = fitz.open(pdf)
    pg = doc[page]
    W, H = pg.rect.width, pg.rect.height
    words = pg.get_text("words")
    doc.close()
    out: dict[str, list[tuple]] = collections.defaultdict(list)
    for w in words:
        x0, y0 = w[0], w[1]
        fx, fy = x0 / W, y0 / H
        if not (clip["left"] <= fx <= clip["right"] and clip["top"] <= fy <= clip["bottom"]):
            continue
        m = _TAG.match(w[4].strip())
        if not m:
            continue
        code = m.group(1).upper()
        if any(abs(px - x0) < 8 and abs(py - y0) < 8 for px, py in out[code]):
            continue
        out[code].append((x0, y0))
    return out


def count_by_code(pdf: str, page: int, clip: dict = _CLIP) -> dict[str, int]:
    """Count (each) = number of distinct callout tags of that code on the plan."""
    return {c: len(pos) for c, pos in _tag_positions(pdf, page, clip).items()}


def _line_segments(pdf: str, page: int):
    """All straight vector segments on the page as (x0,y0,x1,y1) in PDF points."""
    doc = fitz.open(pdf)
    pg = doc[page]
    segs = []
    for d in pg.get_drawings():
        for item in d["items"]:
            if item[0] == "l":            # line
                p1, p2 = item[1], item[2]
                segs.append((p1.x, p1.y, p2.x, p2.y))
            elif item[0] == "re":         # rectangle -> 4 edges
                r = item[1]
                segs += [(r.x0, r.y0, r.x1, r.y0), (r.x1, r.y0, r.x1, r.y1),
                         (r.x1, r.y1, r.x0, r.y1), (r.x0, r.y1, r.x0, r.y0)]
    doc.close()
    return segs


def linear_by_code(pdf: str, page: int, codes: list[str], scale_in_per_ft: float,
                   clip: dict = _CLIP, radius_ft: float = 8.0) -> dict[str, float]:
    """Best-effort linear length (ft) per code: total length of vector segments
    whose midpoint lies within radius_ft of one of that code's tags. Rough — a
    first pass so linear materials appear in the output instead of being missing."""
    tags = _tag_positions(pdf, page, clip)
    segs = _line_segments(pdf, page)
    radius_pt = radius_ft * scale_in_per_ft * 72.0
    out: dict[str, float] = {}
    for code in codes:
        pts = tags.get(code, [])
        if not pts:
            out[code] = 0.0
            continue
        total_pt = 0.0
        for (x0, y0, x1, y1) in segs:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if any(math.hypot(mx - tx, my - ty) <= radius_pt for tx, ty in pts):
                total_pt += math.hypot(x1 - x0, y1 - y0)
        # points -> feet:  length_pt / 72 in/pt  /  (scale in/ft)  = feet
        out[code] = round(total_pt / 72.0 / scale_in_per_ft, 1)
    return out


def _material_plan(pdf: str, page: int, use_vision: bool) -> list[dict]:
    """The per-material schedule. Vision (Gemini) if available, else derive codes
    from the dominant tag family and classify by the deterministic fallback."""
    if use_vision:
        try:
            items = material_plan.read_material_plan(pdf, page)
            if items:
                return items
        except Exception:  # noqa: BLE001 — no key / network: fall back
            pass
    fam = legend.detect_material_family(pdf, page, _CLIP)
    if not fam:
        return []
    return [{"code": c, "name": c, **material_plan.classify_material(c)}
            for c in fam["codes"]]


def build_takeoff(pdf: str, page: int, use_vision: bool = True,
                  scale_in_per_ft: float | None = None,
                  areas: dict | None = None) -> list[dict]:
    """Full raw->QTO for one page: read the plan, detect each material by its
    method, return a unified takeoff (area + linear + count).

    `areas` {code->sqft} may be passed in (e.g. the Stage-2 zone engine already
    ran) to avoid re-running the area engine."""
    plan = _material_plan(pdf, page, use_vision)
    if not plan:
        return []
    if scale_in_per_ft is None:
        from . import stage2
        doc = fitz.open(pdf)
        scale_in_per_ft = 1.0 / stage2.sheet_feet_per_inch(doc[page], default=16.0)
        doc.close()

    by_method = collections.defaultdict(list)
    for m in plan:
        by_method[m["detect"]].append(m)

    quantity: dict[str, float] = {}
    # AREA (closed_area + open_hatch) -> zone engine, restricted to area codes
    area_codes = [m["code"] for m in plan if m["unit"] == "area"]
    if area_codes:
        if areas is None:
            areas = calibrate.detect_area(pdf, page)   # legend-driven engine
        for c in area_codes:
            quantity[c] = round(float(areas.get(c, 0.0)), 1)
    # COUNT (symbol) -> tag occurrences
    counts = count_by_code(pdf, page)
    for m in by_method["symbol"]:
        quantity[m["code"]] = counts.get(m["code"], 0)
    # LINEAR (line) -> vector length near tags
    lin_codes = [m["code"] for m in plan if m["unit"] == "linear"]
    if lin_codes:
        lens = linear_by_code(pdf, page, lin_codes, scale_in_per_ft)
        for c in lin_codes:
            quantity[c] = lens.get(c, 0.0)

    unit_label = {"area": "sq ft", "linear": "ft", "count": "each"}
    return [{"code": m["code"], "name": m["name"], "unit": m["unit"],
             "detect": m["detect"], "quantity": quantity.get(m["code"], 0),
             "unit_label": unit_label[m["unit"]]} for m in plan]
