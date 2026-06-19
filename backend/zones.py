"""Per-zone extraction and rendering.

A *zone* is one connected region of a detected material/element. We extract one
zone per connected component from the Stage-2 label mask (capturing its contour
polygon in base-page PDF points), and we re-render the page overlay + per-code
totals from the set of *active* zones. The zone polygons are the source of
truth, so deleting a zone by id and re-rendering needs no pixel mask.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw

from . import qto_engine

_MIN_ZONE_PX = 120        # drop speckle components
_APPROX_EPS_PX = 1.5      # contour simplification tolerance


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def extract_zones_from_label(label: np.ndarray, codes: list[str], scale: float,
                             dpi: int, source: str = "engine") -> list[dict]:
    """Connected components of each code in `label` -> one zone dict per region.

    Geometry is the external contour, converted raster-px -> PDF points.
    """
    h, w = label.shape
    pt_scale = 72.0 / dpi
    zones: list[dict] = []
    for i, code in enumerate(codes):
        code_mask = (label == (i + 1)).astype(np.uint8)
        if not code_mask.any():
            continue
        n, comp, stats, _ = cv2.connectedComponentsWithStats(code_mask, connectivity=8)
        rgb = qto_engine.code_color_rgb(code)
        for c in range(1, n):
            area_px = int(stats[c, cv2.CC_STAT_AREA])
            if area_px < _MIN_ZONE_PX:
                continue
            m = (comp == c).astype(np.uint8)
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            polys = []
            perim_px = 0.0
            for cnt in cnts:
                perim_px += cv2.arcLength(cnt, True)
                ap = cv2.approxPolyDP(cnt, _APPROX_EPS_PX, True)
                if len(ap) >= 3:
                    polys.append([[float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                                  for p in ap])
            if not polys:
                continue
            x = int(stats[c, cv2.CC_STAT_LEFT]); y = int(stats[c, cv2.CC_STAT_TOP])
            cw = int(stats[c, cv2.CC_STAT_WIDTH]); ch = int(stats[c, cv2.CC_STAT_HEIGHT])
            zones.append({
                "id": uuid.uuid4().hex[:16],
                "code": code,
                "hex": _hex(rgb),
                "area_sqft": round(qto_engine.px_to_sqft(area_px, dpi, scale), 1),
                "perimeter_lf": round(perim_px * (1.0 / dpi) / scale, 1),
                "geometry": polys,
                "bbox": [x / w, y / h, (x + cw) / w, (y + ch) / h],
                "source": source,
            })
    return zones


def groups_from_zones(zones: list[dict]) -> list[dict]:
    """Aggregate active zones by code -> the `groups` list the UI/pricing expect."""
    agg: dict[str, dict] = {}
    for z in zones:
        g = agg.setdefault(z["code"], {"label": z["code"], "sqft": 0.0, "regions": 0,
                                       "hex": z.get("hex")})
        g["sqft"] += z.get("area_sqft") or 0.0
        g["regions"] += 1
    out = [{"label": g["label"], "sqft": round(g["sqft"], 1),
            "regions": g["regions"], "hex": g["hex"]} for g in agg.values()]
    out.sort(key=lambda g: g["sqft"], reverse=True)
    return out


def render_from_zones(pdf_path, page: int, zones: list[dict], out_png: Path,
                      dpi: int = 150) -> dict:
    """Rasterize active zone polygons onto the base page; return recomputed
    groups + the overlay filename."""
    doc = fitz.open(pdf_path)
    pix = doc[page].get_pixmap(dpi=dpi)
    doc.close()
    base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    s = dpi / 72.0
    for z in zones:
        rgb = tuple(int(z["hex"][k:k + 2], 16) for k in (1, 3, 5)) if z.get("hex") \
            else qto_engine.code_color_rgb(z["code"])
        for poly in z.get("geometry") or []:
            sp = [(x * s, y * s) for x, y in poly]
            if len(sp) >= 3:
                od.polygon(sp, fill=(rgb[0], rgb[1], rgb[2], 140),
                           outline=(rgb[0], rgb[1], rgb[2], 255))
    img = Image.alpha_composite(base, overlay).convert("RGB")
    if img.width > 2200:
        img = img.resize((2200, int(img.height * 2200 / img.width)), Image.LANCZOS)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return {"groups": groups_from_zones(zones), "overlay": out_png.name}
