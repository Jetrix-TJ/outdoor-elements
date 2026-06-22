"""Planting-plan takeoff via Gemini 3.1 Pro.

A planting plan's takeoff is a SCHEDULE legend of per-species COUNTS (trees /
palms / shrubs, each) plus AREA items (groundcover / perennial / annual color /
sod, sq ft) — like the human QTO. Plants are labeled individually or in clusters
with a quantity (e.g. "BG 15"), and codes don't map cleanly from text, so we let
gemini-3.1-pro-preview read the sheet and total each code.

    from backend import planting
    counts = planting.count_species(raw_pdf, page)   # {'IC': 44, 'BG': 199, ...}
"""
from __future__ import annotations

import json
import os
import re

import google.generativeai as genai

from . import gemini_config

MODEL = "gemini-3.1-pro-preview"

COUNT_PROMPT = """This is a landscape PLANTING PLAN with a plant SCHEDULE / legend.
Plants are marked on the plan with code labels (e.g. IC, BG, QVE-1, P-FJ, WR).
Some plants are labeled individually; many are CLUSTERS labeled with the code and
a QUANTITY number (e.g. "BG 15" means 15 of plant BG in that cluster).

Count the TOTAL quantity of EACH plant code on this plan — sum the cluster
quantities, and count individually-labeled plants as 1 each.

Return STRICT JSON only, no prose:
[{"code":"<code exactly as written>","count":<integer total>}]"""


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


def _parse_counts(text: str) -> dict[str, int]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[str, int] = {}
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        code = str(d.get("code", "")).strip().upper()
        try:
            n = int(round(float(d.get("count", 0))))
        except (TypeError, ValueError):
            continue
        if code and n > 0:
            out[code] = out.get(code, 0) + n
    return out


def count_species(pdf: str, page: int, api_key: str | None = None,
                  dpi: int = 170, valid_codes: set | None = None) -> dict[str, int]:
    """Total count of each plant code on one planting-plan page (vision). When
    valid_codes is given, only those schedule codes are kept (anchoring)."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    prompt = COUNT_PROMPT
    if valid_codes:
        prompt += "\nOnly use these plant codes: " + ", ".join(sorted(valid_codes))
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": jpeg}])
    c = _parse_counts(resp.text or "")
    if valid_codes:
        vc = {v.upper() for v in valid_codes}
        c = {k: n for k, n in c.items() if k in vc}
    return c


import fitz  # noqa: E402

_CODE_TOK = re.compile(r"^[A-Z]{1,4}(?:-[A-Z0-9]{1,3})?$")


def text_label_counts(pdf: str, page: int, valid_codes: set | None = None) -> dict[str, int]:
    """Number of code LABELS per plant code on the page (deterministic). Accurate
    for individually-labeled plants (trees/palms); a floor for dense beds."""
    doc = fitz.open(pdf)
    words = doc[page].get_text("words")
    doc.close()
    vc = {v.upper() for v in valid_codes} if valid_codes else None
    out: dict[str, int] = {}
    for w in words:
        t = w[4].strip().upper()
        if _CODE_TOK.match(t) and (vc is None or t in vc):
            out[t] = out.get(t, 0) + 1
    return out


def hybrid_counts(pdf: str, pages: list[int], valid_codes: set | None = None,
                  api_key: str | None = None) -> dict[str, int]:
    """Per-species count over several planting pages: max(label count, vision
    count) per code — labels nail individually-marked plants, vision catches the
    denser beds. Anchored to valid_codes (the schedule) when given."""
    total: dict[str, int] = {}
    for pg in pages:
        labels = text_label_counts(pdf, pg, valid_codes)
        try:
            vis = count_species(pdf, pg, api_key=api_key, valid_codes=valid_codes)
        except Exception:  # noqa: BLE001
            vis = {}
        for code in set(labels) | set(vis):
            total[code] = total.get(code, 0) + max(labels.get(code, 0), vis.get(code, 0))
    return total
