"""Per-material 'brain' for RAW plans (vision).

The raw plan's material legend is a GRAPHIC (color/hatch swatch + label), so the
code->name mapping and each material's measurement type can't be read from the
PDF text. This module sends the raw material-plan page to Gemini and gets back a
structured takeoff schedule — for each material code: its name, the quantity UNIT
(area sq ft / linear ft / count each) and a DETECT method hint (closed_area /
open_hatch / line / symbol) telling the detector HOW to measure it.

This is step 1 of raw->QTO ("read the legend to know what to look for"). It
generalizes across project styles (Kirby M.x, Pelican A#, Colleyville (F-101))
because the model reads each project's own legend rather than a hardcoded list.

    from backend import material_plan
    items = material_plan.read_material_plan(raw_pdf, page)   # uses GEMINI_API_KEY
    # -> [{code:'M.5', name:'Concrete Paver A', unit:'area', detect:'closed_area'}, ...]
"""
from __future__ import annotations

import json
import os
import re

import google.generativeai as genai

from . import gemini_config

VALID_UNITS = {"area", "linear", "count"}
VALID_DETECT = {"closed_area", "open_hatch", "line", "symbol"}

PROMPT = """You are reading a construction landscape/hardscape MATERIAL PLAN sheet.
It has a LEGEND/SCHEDULE mapping each material CODE (e.g. M.5, A2, (F-101)) to a
description, and the plan is tagged with those codes.

Return STRICT JSON only — a list, one object per legend material:
[{"code":"<code exactly as written>","name":"<description>",
  "unit":"area|linear|count","detect":"closed_area|open_hatch|line|symbol"}]

How to set unit/detect:
- Paver, concrete, tile, turf, mulch, decking, artificial grass (solid-bordered
  paved areas) -> unit "area", detect "closed_area".
- Gravel, decomposed granite/DG, river rock, pebble, sand, loose stone (hatched
  fills with NO solid border) -> unit "area", detect "open_hatch".
- Wall, border, edging, coping, curb, fence, band, railing, header, screen
  (long thin runs) -> unit "linear", detect "line".
- Bench, planter, boulder, tree, shrub, light, sign, furnishing, individual plant
  -> unit "count", detect "symbol".
Include only real takeoff materials present in the legend. JSON only, no prose."""


def _api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        from dotenv import load_dotenv
        from . import store
        load_dotenv(store.BACKEND_DIR.parent / ".env")
        key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return key


# callout families that are NOT takeoff materials — layout/control points,
# survey marks, sheet annotations. They carry code-like tags and would otherwise
# pollute the takeoff (e.g. the pool set's LP## "construction point" callouts).
_NON_TAKEOFF = re.compile(
    r"\b(construction|control|layout)\s+point|\bdatum\b|bench\s?mark|"
    r"north\s+arrow|grid\s?line|match\s?line|spot\s+elevation|key\s?note|"
    r"property\s+line|setback|limit\s+of\s+work|station\b|control\s+line", re.I)


def is_takeoff_material(name: str) -> bool:
    return not _NON_TAKEOFF.search(name or "")


def _parse_json_list(text: str) -> list[dict]:
    """Pull the JSON array out of the model reply (it may wrap it in ``` fences)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict) or not d.get("code"):
            continue
        if not is_takeoff_material(str(d.get("name", ""))):
            continue   # drop layout/control points & sheet annotations
        unit = str(d.get("unit", "")).lower()
        detect = str(d.get("detect", "")).lower()
        if unit not in VALID_UNITS:
            unit = "area"
        if detect not in VALID_DETECT:
            detect = {"area": "closed_area", "linear": "line", "count": "symbol"}[unit]
        out.append({"code": str(d["code"]).strip(),
                    "name": str(d.get("name", "")).strip(),
                    "unit": unit, "detect": detect})
    return out


def read_material_plan(pdf_path: str, page: int, api_key: str | None = None,
                       dpi: int = 110) -> list[dict]:
    """Vision read of a raw material plan -> structured per-material takeoff plan."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(gemini_config.MODEL_NAME)
    jpeg = gemini_config.render_page_jpeg(pdf_path, page, dpi=dpi)
    resp = model.generate_content([PROMPT, {"mime_type": "image/jpeg", "data": jpeg}])
    return _parse_json_list(resp.text or "")


# ── deterministic fallback: classify a material from its description keywords ──
# Used when Gemini is unavailable, and to sanity-check/repair its output.
# stems matched at a word start (leading \b only) so plurals/suffixes also hit:
# "border"->"Borders", "edg"->"Edging", "boulder"->"Boulders", "wall"->"Walls".
_LINEAR = re.compile(r"\b(wall|border|edg|cop(e|ing)|curb|fenc|band|rail|"
                     r"header|screen|trellis|sill|stair|step)", re.I)
_OPEN_HATCH = re.compile(r"\b(gravel|decomposed granite|\bdg\b|river rock|pebble|"
                         r"sand|loose stone|mulch|crush|aggregate)", re.I)
_COUNT = re.compile(r"\b(bench|planter|boulder|tree|shrub|light|sign|"
                    r"fire ?pit|cabana|column|bollard|pot|fixture|call ?box|"
                    r"monument|arbor|umbrella|table|chair)", re.I)
_AREA = re.compile(r"\b(paver|concrete|tile|turf|grass|deck|sod|artificial|"
                   r"sidewalk|pavement|patio|surface)", re.I)


def classify_material(name: str) -> dict:
    """Best-effort unit/detect from a material description (no vision).

    Shape words win over material words — a "Concrete Border" is a LINEAR run even
    though it's concrete; a "Turf Border" is linear even though it's turf."""
    n = name or ""
    if _LINEAR.search(n):
        return {"unit": "linear", "detect": "line"}
    if _COUNT.search(n):
        return {"unit": "count", "detect": "symbol"}
    if _OPEN_HATCH.search(n):
        return {"unit": "area", "detect": "open_hatch"}
    return {"unit": "area", "detect": "closed_area"}


def summarize(items: list[dict]) -> dict:
    by_unit: dict[str, int] = {}
    for it in items:
        by_unit[it["unit"]] = by_unit.get(it["unit"], 0) + 1
    return by_unit
