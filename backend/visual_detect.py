"""Visual annotation detection via Gemini 3.1 Pro (the lead's approach).

Problem: takeoff callouts use LEADER ARROWS — a code label points (via an arrow)
to the real area/object. Our geometric engine colored near the LABEL, so it
landed on the wrong region, and it never counted trees/pools/spas.

This asks `gemini-3.1-pro-preview` to read the plan image like an estimator and
return, for each annotation, the coordinate the ARROW POINTS TO (the real target),
plus the location of every tree/pool/spa. Gemini's normalized 0-1000 coordinates
are mapped back to PDF points so we color / measure / count exactly there.

    from backend import visual_detect
    anns = visual_detect.detect_annotations(raw_pdf, page)   # uses GEMINI_API_KEY
    # -> [{type:'material'|'tree'|'pool'|'spa', code, point:[y,x], pt:[x_pt,y_pt]}]
    counts = visual_detect.counts(anns)                       # {'tree': 38, ...}
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

import fitz

import google.generativeai as genai

from . import gemini_config

MODEL = "gemini-3.1-pro-preview"
COUNT_TYPES = {"tree", "pool", "spa"}
_TYPES = {"material"} | COUNT_TYPES

PROMPT = """You are an estimator reading a landscape / hardscape construction plan.

1) MATERIAL CALLOUTS: each is a code label (e.g. M.5, A2, (F-101)) joined by a
   LEADER LINE / ARROW to an area of the plan. Return the code and the point the
   arrow POINTS TO (the target area) — NOT the label's own position.
2) COUNT ITEMS: every TREE, POOL and SPA symbol — return each instance's location.

Return STRICT JSON only, no prose:
[{"type":"material|tree|pool|spa","code":"<code or ''>","point":[y,x]}]
point = normalized 0-1000 (y top->bottom, x left->right)."""


def _api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("GEMINI_API_KEY")
    if not key:
        from dotenv import load_dotenv
        from . import store
        load_dotenv(store.BACKEND_DIR.parent / ".env")
        key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return key


def parse_annotations(text: str, width: float, height: float) -> list[dict]:
    """Parse the model reply into annotations with normalized + PDF-point coords.

    `point` stays normalized [y, x] (0-1000); `pt` is [x, y] in PDF points."""
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    mt = re.search(r"\[.*\]", t, re.S)
    if not mt:
        return []
    try:
        data = json.loads(mt.group(0))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        typ = str(d.get("type", "")).lower()
        pt = d.get("point")
        if typ not in _TYPES or not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            continue
        try:
            y, x = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            continue
        out.append({
            "type": typ,
            "code": str(d.get("code", "")).strip(),
            "point": [y, x],
            "pt": [x / 1000.0 * width, y / 1000.0 * height],
        })
    return out


def detect_annotations(pdf: str, page: int, api_key: str | None = None,
                       dpi: int = 150) -> list[dict]:
    """Vision-read one plan page -> annotations with arrow-target coordinates."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([PROMPT, {"mime_type": "image/jpeg", "data": jpeg}])
    doc = fitz.open(pdf)
    r = doc[page].rect
    doc.close()
    return parse_annotations(resp.text or "", r.width, r.height)


def counts(annotations: list[dict]) -> dict:
    """Count of each count-type symbol (tree / pool / spa)."""
    return dict(Counter(a["type"] for a in annotations if a["type"] in COUNT_TYPES))


_COUNT_NAMES = {"tree": "Trees", "pool": "Pools", "spa": "Spas"}


def count_rows(annotations: list[dict]) -> list[dict]:
    """Tree / pool / spa counts as Stage-2 takeoff rows (unit 'count', each).
    Each row carries the symbol points so the overlay can mark them."""
    c = counts(annotations)
    rows = []
    for t in ("tree", "pool", "spa"):
        if c.get(t):
            rows.append({
                "code": "", "name": _COUNT_NAMES[t], "unit": "count",
                "detect": "symbol", "quantity": c[t], "unit_label": "each",
                "source": "visual",
                "points": [a["pt"] for a in annotations if a["type"] == t],
            })
    return rows


def count_takeoff_rows(pdf: str, page: int, api_key: str | None = None,
                       dpi: int = 150) -> tuple[list[dict], list[dict]]:
    """Vision-detect a page and return (count rows, all annotations)."""
    anns = detect_annotations(pdf, page, api_key, dpi)
    return count_rows(anns), anns


def material_targets(annotations: list[dict]) -> list[dict]:
    """Material arrow-targets in the zone engine's tag format ({code, x, y} in PDF
    points) — feed to qto_engine.run_sheet(tags_override=...) so each zone is
    claimed where the arrow POINTS, not at the label (Phase 2)."""
    out = []
    for a in annotations:
        if a.get("type") == "material" and a.get("code") and a.get("pt"):
            out.append({"code": a["code"], "x": float(a["pt"][0]), "y": float(a["pt"][1])})
    return out
