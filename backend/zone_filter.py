"""Filter false-positive zones from engine-detected regions.

Three-tier strategy:
  1. Heuristic auto-drop: legend strip (x0 > 0.75) and sub-noise (< 3 sqft).
  2. Heuristic auto-keep: large zones (>= 50 sqft) inside the drawing area.
  3. Gemini vision: ambiguous zones get a single annotated-image API call.
     Fallback on any error: keep all ambiguous zones.
"""
from __future__ import annotations

import io
import json
import logging
import warnings
from pathlib import Path

log = logging.getLogger(__name__)

LEGEND_STRIP_X = 0.75
AUTO_KEEP_SQFT = 50.0
AUTO_DROP_SQFT = 3.0


def filter_false_positives(
    zones: list[dict],
    pdf_path,
    page: int,
    dpi: int,
    scale_in_per_ft: float,
    api_key: str | None,
) -> list[dict]:
    """Filter legend/table false-positive zones using heuristics + optional Gemini vision.

    Args:
        zones: list of zone dicts (full schema, as stored in DB)
        pdf_path: path to the job's PDF
        page: 0-based page index
        dpi: DPI used for rendering (matches detection DPI, typically 150)
        scale_in_per_ft: drawing scale (inches per foot)
        api_key: Gemini API key; if None, skip Gemini pass

    Returns:
        Filtered zone list (subset of input zones, same dicts, same order relative to kept zones).
    """
    if not zones:
        return zones

    definite_keep: list[dict] = []
    ambiguous: list[dict] = []

    for z in zones:
        x0, _y0, x1, _y1 = z.get("bbox", [0, 0, 1, 1])
        area = z.get("area_sqft", 0) or 0

        if x0 > LEGEND_STRIP_X:
            pass  # definite_drop — legend/title strip
        elif area < AUTO_DROP_SQFT:
            pass  # definite_drop — sub-noise
        elif area >= AUTO_KEEP_SQFT and x1 <= LEGEND_STRIP_X:
            definite_keep.append(z)
        else:
            ambiguous.append(z)

    if not ambiguous:
        return definite_keep

    if not api_key:
        return definite_keep + ambiguous

    try:
        real_ids = _classify_with_gemini(ambiguous, pdf_path, page, dpi, api_key)
        kept_ambiguous = [z for z in ambiguous if z["id"] in real_ids]
    except Exception as exc:
        log.warning(
            "zone_filter: Gemini classify failed (%s) — keeping all ambiguous zones", exc
        )
        kept_ambiguous = ambiguous

    return definite_keep + kept_ambiguous


def _render_annotated(pdf_path, page: int, dpi: int, zones: list[dict]) -> bytes:
    """Render page PNG with numbered red boxes over ambiguous zones."""
    import fitz
    from PIL import Image, ImageDraw

    render_dpi = min(dpi, 100)
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page].get_pixmap(dpi=render_dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    W, H = img.size
    draw = ImageDraw.Draw(img)
    for i, z in enumerate(zones):
        x0, y0, x1, y1 = z["bbox"]
        px = [x0 * W, y0 * H, x1 * W, y1 * H]
        draw.rectangle(px, outline="red", width=2)
        draw.text((int(px[0]) + 2, int(px[1]) + 2), str(i + 1), fill="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _classify_with_gemini(
    zones: list[dict], pdf_path, page: int, dpi: int, api_key: str
) -> set[str]:
    """Send annotated page image to Gemini and return IDs of 'real' zones.

    Uses the same google.generativeai import pattern as gemini_config.py.
    """
    import google.generativeai as genai

    img_bytes = _render_annotated(pdf_path, page, dpi, zones)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = (
        f"You are reviewing a construction plan drawing. "
        f"Red numbered boxes (1 through {len(zones)}) highlight colored regions.\n"
        "Each box may be a real surface/material area OR a false positive "
        "(legend swatch, schedule cell, title block fill, small decorative element).\n\n"
        "For each numbered box, classify as:\n"
        '- "real": a meaningful floor/surface/material zone in the takeoff\n'
        '- "fp": false positive (legend, table, label, decoration, border)\n\n'
        'Respond ONLY with valid JSON: {"1": "real", "2": "fp", ...}'
    )

    # Use the same image-part pattern as gemini_config.py
    image_part = {"mime_type": "image/png", "data": img_bytes}
    response = model.generate_content([prompt, image_part])
    raw = response.text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    classifications = json.loads(raw)

    real_ids: set[str] = set()
    for i, z in enumerate(zones):
        key = str(i + 1)
        if classifications.get(key) == "real":
            real_ids.add(z["id"])
    return real_ids
