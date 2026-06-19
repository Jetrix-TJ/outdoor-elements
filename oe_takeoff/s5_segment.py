"""Stage 5 - detect each material's surface region.

PRIMARY (vector): the PDF carries the material fills as vector polygons whose
fill color is the material's true (saturated) color. We resolve each code's
true fill color from its legend swatch rectangle (stage 3), then sum the area
of every plan polygon with that color. This is exact geometry - no
anti-aliasing, no gray-linework collision, and M.6/M.15 separate cleanly
because their true fill colors differ. This is the deterministic anchor.

FALLBACK (raster): OpenCV color segmentation around the sampled swatch color,
kept for sheets without a usable vector layer and as a cross-check. The light
-blue family is segmented together and split by geometry.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from . import config
from .models import LegendItem, Region

_CODE_RE = re.compile(r"^\([A-Z]+\.\d+\)$")


# ===========================================================================
# PRIMARY: vector polygon detection
# ===========================================================================
def _to255(c):
    return None if c is None else tuple(int(round(v * 255)) for v in c)


def _poly_area_pt2(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _drawing_polys(d: dict) -> list[list[tuple[float, float]]]:
    """Filled subpaths of a drawing as point lists (PDF points)."""
    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for it in d["items"]:
        t = it[0]
        if t == "re":
            r = it[1]
            polys.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
        elif t == "qu":
            q = it[1]
            polys.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                          (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
        elif t == "l":
            if not cur:
                cur.append((it[1].x, it[1].y))
            cur.append((it[2].x, it[2].y))
        elif t == "c":
            if not cur:
                cur.append((it[1].x, it[1].y))
            cur.append((it[4].x, it[4].y))
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def _legend_bbox_pts(ctx: dict):
    """Legend box bounds in PDF points (to exclude swatches/legend fills)."""
    words = ctx["words"]
    codes = [t for t in words if _CODE_RE.match(t[4].strip())]
    if not codes:
        return None
    xs = np.array([t[0] for t in codes])
    x_col = np.median(xs)
    col = [t for t in codes if abs(t[0] - x_col) < 20] or codes
    return (min(t[0] for t in col) - 0.7 * 72, min(t[1] for t in col) - 0.5 * 72,
            max(t[2] for t in col) + 3.5 * 72, max(t[3] for t in col) + 0.5 * 72)


def _color_match(a, b, tol: int) -> bool:
    return a is not None and b is not None and max(abs(a[i] - b[i]) for i in range(3)) <= tol


def _is_marker(rect, sqft: float, footprint_counts: dict) -> bool:
    """A small, near-square fill stamped at a footprint shared by many siblings
    is a symbol/keynote marker, not a material area."""
    if rect is None or sqft >= config.MARKER_MAX_SQFT:
        return False
    w, h = rect.width, rect.height
    if h <= 0:
        return False
    lo, hi = config.MARKER_ASPECT_RANGE
    near_square = lo <= (w / h) <= hi
    repeated = footprint_counts.get((round(w), round(h)), 0) >= config.MARKER_MIN_REPEAT
    return near_square and repeated


def _regions_for_color(drawings, fill_rgb, spp2, ppi, in_legend):
    """All non-marker polygon regions of one fill color (markers removed)."""
    from collections import Counter

    cands = [d for d in drawings
             if _color_match(_to255(d.get("fill")), fill_rgb, config.VECTOR_COLOR_TOL)
             and not in_legend(d.get("rect"))]
    footprint_counts = Counter(
        (round(d["rect"].width), round(d["rect"].height)) for d in cands if d.get("rect")
    )
    regions: list[Region] = []
    markers = 0
    for d in cands:
        d_sqft = sum(_poly_area_pt2(poly) for poly in _drawing_polys(d)) * spp2
        if _is_marker(d.get("rect"), d_sqft, footprint_counts):
            markers += 1
            continue
        for poly in _drawing_polys(d):
            area_pt2 = _poly_area_pt2(poly)
            sqft = area_pt2 * spp2
            if sqft < 0.5:
                continue
            px = [[int(x * ppi), int(y * ppi)] for x, y in poly]
            xs = [p[0] for p in px]
            ys = [p[1] for p in px]
            regions.append(Region(
                bbox=[min(xs), min(ys), max(xs), max(ys)],
                pixel_area=int(area_pt2 * ppi * ppi),
                sqft=round(sqft, 2), contour=px,
            ))
    return regions, markers


def detect_vector(ctx: dict, items: list[LegendItem], calib: dict) -> dict:
    """Sum vector-polygon area per material by true fill color (markers removed).

    Items sharing an identical fill color (different materials, same color) are
    grouped: the combined regions are reported for each member and flagged as
    'shared_color' so stage 6 (Gemini) can attribute them spatially.
    """
    drawings = ctx.get("drawings", [])
    ppi = ctx["ppi"]
    spp2 = calib.get("sqft_per_point2", config.sqft_per_point2(calib.get("feet_per_inch", 10.0)))
    lb = _legend_bbox_pts(ctx)

    def in_legend(rect) -> bool:
        if lb is None or rect is None:
            return False
        cx, cy = (rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0
        return lb[0] <= cx <= lb[2] and lb[1] <= cy <= lb[3]

    # group items by fill color to detect collisions
    by_color: dict[tuple, list[LegendItem]] = {}
    for it in items:
        if it.fill_rgb and it.fill_rgb not in config.VECTOR_IGNORE_COLORS:
            by_color.setdefault(it.fill_rgb, []).append(it)

    result: dict[str, dict] = {}
    for it in items:
        if it.fill_rgb is None or it.fill_rgb in config.VECTOR_IGNORE_COLORS:
            result[it.code] = {"regions": [], "sqft": 0.0, "fill_rgb": it.fill_rgb,
                               "source": "vector", "markers_removed": 0,
                               "note": "no fill color resolved"}
            continue
        group = by_color[it.fill_rgb]
        regions, markers = _regions_for_color(drawings, it.fill_rgb, spp2, ppi, in_legend)
        entry = {
            "regions": regions,
            "sqft": round(sum(r.sqft for r in regions), 2),
            "fill_rgb": it.fill_rgb,
            "source": "vector",
            "markers_removed": markers,
        }
        if len(group) > 1:
            others = [g.code for g in group if g.code != it.code]
            entry["shared_color"] = others
            entry["note"] = f"shares fill color with {','.join(others)}; combined area (needs split)"
        result[it.code] = entry
    return result


# ===========================================================================
# FALLBACK: raster color segmentation (kept as cross-check)
# ===========================================================================
def legend_exclusion_mask(ctx: dict) -> np.ndarray:
    rgb, words, ppi = ctx["rgb"], ctx["words"], ctx["ppi"]
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    codes = [t for t in words if _CODE_RE.match(t[4].strip())]
    if not codes:
        return mask
    xs = np.array([t[0] for t in codes])
    x_col = np.median(xs)
    col = [t for t in codes if abs(t[0] - x_col) < 20] or codes
    x0 = min(t[0] for t in col) - 0.55 * 72
    x1 = max(t[2] for t in col) + 3.2 * 72
    y0 = min(t[1] for t in col) - 0.4 * 72
    y1 = max(t[3] for t in col) + 0.4 * 72
    px = lambda v: int(v * ppi)
    mask[max(0, px(y0)):min(h, px(y1)), max(0, px(x0)):min(w, px(x1))] = True
    return mask


def _color_mask(rgb: np.ndarray, swatch: tuple[int, int, int], tol: int) -> np.ndarray:
    diff = np.abs(rgb.astype(np.int16) - np.array(swatch, dtype=np.int16))
    return (diff.max(axis=2) <= tol)


def _clean(mask: np.ndarray, close_px: int) -> np.ndarray:
    m = mask.astype(np.uint8)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    return m.astype(bool)


def _regions(mask: np.ndarray, sqft_per_px: float, min_sqft: float) -> list[Region]:
    m = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out: list[Region] = []
    for i in range(1, n):
        area_px = int(stats[i, cv2.CC_STAT_AREA])
        sqft = area_px * sqft_per_px
        if sqft < min_sqft:
            continue
        x, y, ww, hh = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                        int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        out.append(Region(bbox=[x, y, x + ww, y + hh], pixel_area=area_px, sqft=round(sqft, 2)))
    return out


def segment_raster(ctx, items, calib, rgb_tol=config.DEFAULT_RGB_TOL,
                   close_px=config.DEFAULT_CLOSE_PX,
                   min_region_sqft=config.DEFAULT_MIN_REGION_SQFT) -> dict:
    rgb = ctx["rgb"]
    spp = calib["sqft_per_pixel"]
    excl = legend_exclusion_mask(ctx)
    result: dict[str, dict] = {}

    for it in items:
        if it.code in config.BLUE_FAMILY:
            continue
        mask = _clean(_color_mask(rgb, it.swatch_rgb, rgb_tol) & ~excl, close_px)
        regions = _regions(mask, spp, min_region_sqft)
        result[it.code] = {"mask": mask, "regions": regions,
                           "sqft": round(sum(r.sqft for r in regions), 2), "source": "raster"}

    blue_items = [it for it in items if it.code in config.BLUE_FAMILY]
    if blue_items:
        avg = tuple(int(np.mean([it.swatch_rgb[c] for it in blue_items])) for c in range(3))
        combined = _clean(_color_mask(rgb, avg, rgb_tol) & ~excl, close_px)
        regions = _regions(combined, spp, min_region_sqft)
        regions_sorted = sorted(regions, key=lambda r: r.pixel_area, reverse=True)
        big = [regions_sorted[0]] if regions_sorted else []
        small = regions_sorted[1:]
        for code, regs in (("M.6", big), ("M.15", small)):
            result[code] = {"mask": combined, "regions": regs,
                            "sqft": round(sum(r.sqft for r in regs), 2),
                            "split_heuristic": "largest-blob=M.6", "source": "raster"}
        result["_blue_combined"] = {"mask": combined, "regions": regions,
                                    "sqft": round(sum(r.sqft for r in regions), 2)}
    return result
