"""Pool / spa surface detection — color-first, vision-guided.

Strategy:
  1. Render the page as an image.
  2. Gemini identifies which fill colors represent which surfaces
     (POOL, SPA, TANNING LEDGE, …) by reading the drawing labels/legend.
     It also returns the approximate RGB for each surface color.
  3. Color-range segmentation (HSV) extracts pixel masks for each surface.
  4. Contours are computed from those masks.
  5. Scale is read from the title-block text.

This handles both construction drawings that are purely line-based (where the
pool boundary forms an enclosed shape) and color-coded plans where different
fills represent different surfaces.  It does NOT require an estimate PDF.
"""
from __future__ import annotations

import io
import json
import logging
import re
import uuid
import warnings
from pathlib import Path

import cv2
import numpy as np

from . import zones as zones_mod

log = logging.getLogger(__name__)

_SURFACE_COLOR = {
    "POOL":           (233, 30,  99),
    "SPA":            (0,  150, 136),
    "TANNING LEDGE":  (255, 193,  7),
    "SUN SHELF":      (255, 193,  7),
    "STONE STEPPERS": (121,  85, 72),
    "STEPS":          (121,  85, 72),
    "BENCH":          (96, 125, 139),
    "DECK":           (189, 189, 189),
    "COPING":         (158, 158, 158),
}
_DEFAULT_COLOR = (158, 158, 158)

_IGNORE = {"BACKGROUND", "FP", "IGNORE", "NONE", "SCHEDULE", "TITLE",
           "TABLE", "NOTES", "BORDER", "OVERALL"}


def detect_pool(pdf_path, page_idx: int, out_png, dpi: int = 150,
                api_key: str | None = None, clip_right: float = 0.78) -> dict:
    """Detect & measure pool/spa surfaces on one plan page.

    Uses Gemini vision to identify which fill colors correspond to which
    surfaces, then uses color-range segmentation to find each surface's
    geometry.  Falls back to size-rank connected-component detection when
    no API key is available.

    Returns:
        {surfaces, overlay, scale_in_per_ft, zones}
    """
    # ── 1. Read scale from title-block text ──────────────────────────────────
    scale_in_per_ft = _read_scale(pdf_path, page_idx)
    px_per_sf = (scale_in_per_ft * dpi) ** 2

    # ── 2. Render page image ─────────────────────────────────────────────────
    img_bytes = _render_page(pdf_path, page_idx, dpi)

    # decode to numpy BGR
    img_arr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
    h, w = img_bgr.shape[:2]

    # clip legend/title-block column
    clip_w = int(w * clip_right)
    drawing = img_bgr[:, :clip_w].copy()

    # ── 3. Identify surfaces via Gemini (two strategies) ─────────────────────
    surfaces_info: list[dict] = []   # colour-coded plan path
    regions_info:  list[dict] = []   # line-drawing plan path

    if api_key:
        try:
            surfaces_info = _gemini_identify_colors(img_bytes, api_key)
        except Exception as exc:
            log.warning("pool_mode: Gemini color ID failed (%s) — trying region ID", exc)

        if not surfaces_info:
            # No coloured fills found — likely a line-drawing construction doc.
            # Ask Gemini for bounding boxes instead.
            try:
                regions_info = _gemini_identify_regions(img_bytes, api_key)
            except Exception as exc:
                log.warning("pool_mode: Gemini region ID failed (%s) — using boundary fallback", exc)

    if surfaces_info:
        zone_list = _zones_from_colors(drawing, surfaces_info, scale_in_per_ft, dpi, clip_w, w, h)
    elif regions_info:
        zone_list = _zones_from_regions(regions_info, scale_in_per_ft, dpi, w, h)
    else:
        zone_list = _zones_from_boundaries(pdf_path, page_idx, dpi, scale_in_per_ft, clip_right)

    zones_mod.render_from_zones(str(pdf_path), page_idx, zone_list, Path(out_png), dpi=dpi)

    return {
        "surfaces": [
            {"name": z["code"], "area_sf": z["area_sqft"],
             "perimeter_lf": z["perimeter_lf"]}
            for z in zone_list
        ],
        "overlay":         Path(out_png).name,
        "scale_in_per_ft": scale_in_per_ft,
        "zones":           zone_list,
    }


# ── Gemini color identification ───────────────────────────────────────────────

def _gemini_identify_colors(img_bytes: bytes, api_key: str) -> list[dict]:
    """Ask Gemini which fill colors map to which pool surfaces.

    Returns list of {name, color_rgb: [R,G,B], tolerance} dicts.
    Only real surfaces are returned; background/border fills are excluded.
    """
    import google.generativeai as genai

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-3.5-flash")

    prompt = (
        "This is a pool/spa construction plan drawing.\n\n"
        "Identify every distinct COLORED FILL AREA that represents a real pool surface "
        "(POOL water area, SPA water area, TANNING LEDGE, SUN SHELF, STEPS, BENCH, "
        "STONE STEPPERS, DECK, COPING, etc.).\n\n"
        "For each surface:\n"
        "- Read the text label printed DIRECTLY ON the colored region, or check the drawing legend.\n"
        "- Return its dominant fill color as an RGB list [R, G, B] (0-255).\n\n"
        "IMPORTANT RULES:\n"
        "- If a large colored fill covers most of the page, it is the BACKGROUND — exclude it.\n"
        "- The SPA complex usually has an INNER water fill AND an OUTER surround fill "
        "— if both are the same or similar color, include both as SPA.\n"
        "- Do NOT include white/light-gray fills (those are paper/grid).\n"
        "- If the page is a PIPING, EQUIPMENT, or DETAIL drawing with no pool surface fills, "
        'return {"surfaces": []}.\n\n'
        "Respond ONLY with valid JSON:\n"
        '{"surfaces": [{"name": "POOL", "color_rgb": [R,G,B]}, '
        '{"name": "SPA", "color_rgb": [R,G,B]}, ...]}'
    )

    image_part = {"mime_type": "image/png", "data": img_bytes}
    response = model.generate_content([prompt, image_part])
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    data = json.loads(raw)
    return [s for s in data.get("surfaces", [])
            if s.get("name", "").upper() not in _IGNORE and s.get("color_rgb")]


def _gemini_identify_regions(img_bytes: bytes, api_key: str) -> list[dict]:
    """Ask Gemini to trace pool surface outlines in a line-drawing construction plan.

    Used when no coloured fills are found.  Gemini reads the TOP-DOWN PLAN VIEW
    and returns the corner points of each surface as a polygon.  Area is then
    computed with the shoelace formula — no pixel flood-fill needed.

    Returns list of {name, polygon: [[x,y], ...]} with coords normalised to
    [0,1] relative to the full page image.
    """
    import google.generativeai as genai

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-3.5-flash")

    prompt = (
        "This is a pool/spa construction plan drawing. The page may contain "
        "multiple views: a top-down PLAN VIEW, cross-sections, elevations, and "
        "detail drawings.\n\n"
        "Focus ONLY on the TOP-DOWN PLAN VIEW — the overhead view that shows "
        "the pool shape from above (labeled 'POOL PLAN' or similar).\n\n"
        "In the plan view, identify each DISTINCT WATER/SURFACE AREA:\n"
        "- POOL  (main pool water area — the largest enclosed shape)\n"
        "- SPA   (hot tub / spa, usually a smaller adjacent shape)\n"
        "- TANNING LEDGE or SUN SHELF (shallow raised shelf, if present)\n"
        "- STEPS or BENCH (recessed steps / bench, if separately bounded)\n\n"
        "For EACH surface, trace its OUTER WATER EDGE as a polygon.  Return "
        "4–20 corner points (follow the actual shape — rectangular needs 4 "
        "corners, L-shaped needs 6, etc.).\n\n"
        "Coordinates are NORMALISED to the FULL PAGE: (0,0) = top-left corner "
        "of the full page image, (1,1) = bottom-right corner.\n\n"
        "IMPORTANT:\n"
        "- Trace the WATER SURFACE boundary, not the structural wall.\n"
        "- Do NOT include sections, elevations, or details.\n"
        "- If this page is a PIPING, EQUIPMENT, or DETAIL drawing with no "
        '  plan view, return {"surfaces": []}.\n\n'
        "Return ONLY valid JSON (no markdown):\n"
        '{"surfaces": [\n'
        '  {"name": "POOL", "polygon": [[x1,y1],[x2,y2],...,[xn,yn]]},\n'
        '  {"name": "SPA",  "polygon": [[x1,y1],...,[xn,yn]]}\n'
        "]}"
    )

    image_part = {"mime_type": "image/png", "data": img_bytes}
    response = model.generate_content([prompt, image_part])
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    data = json.loads(raw)
    out = []
    for s in data.get("surfaces", []):
        name = s.get("name", "").upper()
        poly = s.get("polygon", [])
        if name in _IGNORE or len(poly) < 3:
            continue
        # Clamp coords to [0,1]
        poly = [[max(0.0, min(1.0, float(p[0]))),
                 max(0.0, min(1.0, float(p[1])))] for p in poly]
        out.append({"name": name, "polygon": poly})
    return out


# ── Color-range zone extraction ───────────────────────────────────────────────

def _color_mask(img_bgr: np.ndarray, rgb: list, tolerance: int = 35) -> np.ndarray:
    """Binary mask for pixels within `tolerance` of the given RGB color."""
    r, g, b = rgb
    target = np.array([b, g, r], dtype=np.float32)
    diff = np.abs(img_bgr.astype(np.float32) - target)
    dist = np.max(diff, axis=2)
    return (dist <= tolerance).astype(np.uint8) * 255


def _zones_from_colors(
    drawing: np.ndarray,
    surfaces_info: list[dict],
    scale_in_per_ft: float,
    dpi: int,
    clip_w: int,
    full_w: int,
    full_h: int,
) -> list[dict]:
    """Build zone dicts from color-range masks for each identified surface."""
    px_per_sf = (scale_in_per_ft * dpi) ** 2
    pt_scale = 72.0 / dpi
    h, w = drawing.shape[:2]

    # deduplicate by name — merge multiple entries with same name
    by_name: dict[str, list] = {}
    for s in surfaces_info:
        name = s["name"].upper()
        by_name.setdefault(name, []).append(s["color_rgb"])

    zone_list: list[dict] = []
    used_mask = np.zeros((h, w), dtype=np.uint8)

    for name, color_list in by_name.items():
        if name in _IGNORE:
            continue

        # combine masks for all color variants of this surface
        combined = np.zeros((h, w), dtype=np.uint8)
        for rgb in color_list:
            combined = cv2.bitwise_or(combined, _color_mask(drawing, rgb))

        # close small holes, remove specks
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k, iterations=3)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN,  k, iterations=2)

        # don't overlap zones already assigned
        combined[used_mask > 0] = 0

        # find contours
        cnts, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        rgb_out = _SURFACE_COLOR.get(name, _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb_out

        for cnt in cnts:
            area_px = cv2.contourArea(cnt)
            if area_px < 0.0005 * h * w:   # < 0.05 % of drawing — skip specks
                continue

            # mark as used
            cv2.drawContours(used_mask, [cnt], -1, 255, -1)

            simplified = cv2.approxPolyDP(cnt, 2.0, True)
            polys = []
            if len(simplified) >= 3:
                polys = [[[float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                           for p in simplified]]

            x, y, cw, ch = cv2.boundingRect(cnt)
            bbox = [x / full_w, y / full_h,
                    (x + cw) / full_w, (y + ch) / full_h]

            if not polys:
                polys = [[[int(x * pt_scale),       int(y * pt_scale)],
                           [int((x+cw) * pt_scale),  int(y * pt_scale)],
                           [int((x+cw) * pt_scale),  int((y+ch) * pt_scale)],
                           [int(x * pt_scale),        int((y+ch) * pt_scale)]]]

            perim_px = cv2.arcLength(cnt, True)
            area_sf   = round(area_px / px_per_sf, 1)
            perim_lf  = round(perim_px * (1.0 / dpi) / scale_in_per_ft, 1)

            zone_list.append({
                "id":           uuid.uuid4().hex[:16],
                "code":         name,
                "hex":          hex_color,
                "area_sqft":    area_sf,
                "perimeter_lf": perim_lf,
                "geometry":     polys,
                "bbox":         bbox,
                "source":       "pool",
                "status":       "active",
            })

    return zone_list


# ── Polygon-based extraction for line drawings ────────────────────────────────

def _zones_from_regions(
    regions: list[dict],
    scale_in_per_ft: float,
    dpi: int,
    img_w: int,
    img_h: int,
) -> list[dict]:
    """Convert Gemini-traced polygon outlines into zone dicts.

    Each region carries a normalised polygon (Gemini traced the water edge
    in the plan view).  Area is computed with the shoelace formula on pixel
    coordinates, then converted to sqft.  No pixel flood-fill needed.
    """
    px_per_sf = (scale_in_per_ft * dpi) ** 2
    pt_scale  = 72.0 / dpi          # image pixels → PDF points
    zone_list: list[dict] = []

    for region in regions:
        name = region["name"].upper()
        if name in _IGNORE:
            continue

        norm_pts = region["polygon"]    # [[x,y], ...] normalised [0,1]
        if len(norm_pts) < 3:
            continue

        # Convert to image pixels
        px_pts = [[p[0] * img_w, p[1] * img_h] for p in norm_pts]

        # Area via shoelace (absolute value)
        n = len(px_pts)
        area_px = 0.0
        for i in range(n):
            j = (i + 1) % n
            area_px += px_pts[i][0] * px_pts[j][1]
            area_px -= px_pts[j][0] * px_pts[i][1]
        area_px = abs(area_px) / 2.0

        if area_px < 100:
            log.warning("pool_mode: region %s area too small (%d px) — skip", name, int(area_px))
            continue

        # Perimeter
        perim_px = 0.0
        for i in range(n):
            j = (i + 1) % n
            dx = px_pts[j][0] - px_pts[i][0]
            dy = px_pts[j][1] - px_pts[i][1]
            perim_px += (dx*dx + dy*dy) ** 0.5

        # Convert polygon to PDF points for geometry storage
        poly_pts = [[p[0] * pt_scale, p[1] * pt_scale] for p in px_pts]

        xs = [p[0] for p in px_pts]
        ys = [p[1] for p in px_pts]
        bbox_out = [min(xs)/img_w, min(ys)/img_h,
                    max(xs)/img_w, max(ys)/img_h]

        rgb_out   = _SURFACE_COLOR.get(name, _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb_out
        area_sf   = round(area_px  / px_per_sf, 1)
        perim_lf  = round(perim_px * (1.0 / dpi) / scale_in_per_ft, 1)

        zone_list.append({
            "id":           uuid.uuid4().hex[:16],
            "code":         name,
            "hex":          hex_color,
            "area_sqft":    area_sf,
            "perimeter_lf": perim_lf,
            "geometry":     [poly_pts],
            "bbox":         bbox_out,
            "source":       "pool",
            "status":       "active",
        })

    return zone_list


# ── Boundary-based fallback (no API key) ─────────────────────────────────────

def _zones_from_boundaries(
    pdf_path, page_idx: int, dpi: int,
    scale_in_per_ft: float, clip_right: float,
) -> list[dict]:
    """Fallback: connected-component detection on thick boundary lines.
    Names zones by size rank (POOL=largest, SPA=second, etc.)."""
    from . import qto_engine

    boundary = qto_engine.render_thick_boundaries(pdf_path, page_idx, dpi, min_lw=0.18)
    binary   = qto_engine.preprocess_for_fill(boundary)
    h, w     = binary.shape
    binary[:, int(w * clip_right):] = 0

    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=4)

    # exclude background (region 0) and extremely large or tiny regions
    cands = [
        (i, int(stats[i, cv2.CC_STAT_AREA]))
        for i in range(1, n)
        if 0.001 * h * w < int(stats[i, cv2.CC_STAT_AREA]) < 0.50 * h * w
    ]
    if not cands:
        return []

    _DEFAULTS = ["POOL", "SPA", "TANNING LEDGE", "STONE STEPPERS"]
    sorted_cands = sorted(cands, key=lambda x: -x[1])

    px_per_sf = (scale_in_per_ft * dpi) ** 2
    pt_scale  = 72.0 / dpi
    zone_list: list[dict] = []

    for rank, (idx, area_px) in enumerate(sorted_cands):
        name = _DEFAULTS[rank] if rank < len(_DEFAULTS) else "BACKGROUND"
        if name in _IGNORE:
            continue

        mask = (labels == idx).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perim_px = sum(cv2.arcLength(c, True) for c in cnts)

        polys = []
        for cnt in cnts:
            simplified = cv2.approxPolyDP(cnt, 2.0, True)
            if len(simplified) >= 3:
                polys.append([[float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                               for p in simplified])

        x0   = int(stats[idx, cv2.CC_STAT_LEFT])
        y0   = int(stats[idx, cv2.CC_STAT_TOP])
        cw   = int(stats[idx, cv2.CC_STAT_WIDTH])
        ch_  = int(stats[idx, cv2.CC_STAT_HEIGHT])
        bbox = [x0/w, y0/h, (x0+cw)/w, (y0+ch_)/h]

        if not polys:
            polys = [[[int(x0*pt_scale), int(y0*pt_scale)],
                       [int((x0+cw)*pt_scale), int(y0*pt_scale)],
                       [int((x0+cw)*pt_scale), int((y0+ch_)*pt_scale)],
                       [int(x0*pt_scale), int((y0+ch_)*pt_scale)]]]

        rgb_out   = _SURFACE_COLOR.get(name, _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb_out
        area_sf   = round(area_px / px_per_sf, 1)
        perim_lf  = round(perim_px * (1.0/dpi) / scale_in_per_ft, 1)

        zone_list.append({
            "id":           uuid.uuid4().hex[:16],
            "code":         name,
            "hex":          hex_color,
            "area_sqft":    area_sf,
            "perimeter_lf": perim_lf,
            "geometry":     polys,
            "bbox":         bbox,
            "source":       "pool",
            "status":       "active",
        })

    return zone_list


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render_page(pdf_path, page_idx: int, dpi: int) -> bytes:
    """Render a PDF page to PNG bytes."""
    import fitz
    render_dpi = min(dpi, 120)
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_idx].get_pixmap(dpi=render_dpi)
    return pix.tobytes("png")


def _read_scale(pdf_path, page_idx: int) -> float:
    """Parse drawing scale from title-block text. Returns inches per foot."""
    import fitz
    with fitz.open(str(pdf_path)) as doc:
        text = doc[page_idx].get_text().upper()

    # "1/4" = 1'-0"" or "3/16" = 1'" etc.
    m = re.search(r'(\d+)/(\d+)\s*["“”]?\s*=\s*1\s*[\'’\-]', text)
    if m:
        return int(m.group(1)) / int(m.group(2))

    # engineer's scale "1" = 10'"
    m = re.search(r'(\d+)\s*["“”]\s*=\s*(\d+)\s*[\'’\-]', text)
    if m:
        return int(m.group(1)) / int(m.group(2))

    log.warning("pool_mode: could not parse scale on page %d — defaulting to 1/4\"=1'", page_idx)
    return 0.25
