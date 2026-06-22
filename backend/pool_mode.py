"""Pool / spa surface detection — estimate-guided, shape-based.

Raw pool sheets have no callout tags (unlike landscape's M.x), so we anchor on
the *dominant enclosed regions* of the pool plan: flood-fill them (connected
components of the boundary raster), match each to an estimate target by area,
then measure area (SF) and perimeter (LF, for coping / waterline). The estimate
is the guide & validator — e.g. POOL ~1,109 SF, SPA ~161 SF, coping ~200 LF.

This mirrors the landscape estimate-guided idea (target quantities drive the
selection); the only difference is the anchor is the shape, not a tag.
"""
from __future__ import annotations

from pathlib import Path

import uuid

import cv2
import numpy as np

from . import qto_engine
from . import zones as zones_mod

# distinct fill color per pool surface (RGB), echoing the human's QTO scheme
_SURFACE_COLOR = {
    "POOL": (233, 30, 99),          # magenta
    "SPA": (0, 150, 136),           # teal
    "TANNING LEDGE": (255, 193, 7),  # amber
    "STONE STEPPERS": (121, 85, 72),
}
_DEFAULT_COLOR = (158, 158, 158)


def detect_pool(pdf_path, page_idx, targets: dict, out_png, dpi: int = 150,
                scale_in_per_ft: float | None = None, nominal_scale: float = 0.25,
                clip_right: float = 0.78) -> dict:
    """Detect & measure pool/spa surfaces on one plan page.

    targets: {surface_name: target_sqft} from the estimate (e.g. {"POOL": 1109}).
    scale_in_per_ft: drawing inches per real foot. If None, it is *calibrated
        from the estimate targets* (least-squares over the matched regions), so
        all surfaces land close to the human — the estimate is the known scale
        reference. Returns {surfaces:[{name,area_sf,perimeter_lf}], overlay, scale}."""
    boundary = qto_engine.render_thick_boundaries(pdf_path, page_idx, dpi, min_lw=0.18)
    binary = qto_engine.preprocess_for_fill(boundary)
    h, w = binary.shape
    binary[:, int(w * clip_right):] = 0   # drop the title-block / schedule column

    n, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, connectivity=4)

    # candidate enclosed regions: not the whole sheet, not specks
    cands = [(i, int(stats[i, cv2.CC_STAT_AREA])) for i in range(1, n)
             if 0.002 * h * w < int(stats[i, cv2.CC_STAT_AREA]) < 0.35 * h * w]

    # Pass 1 — at a nominal scale, match each target to the unused region whose
    # area is closest (this picks the right shape, skipping deck/schedule boxes).
    nom_px_per_sf = ((scale_in_per_ft or nominal_scale) * dpi) ** 2
    matched: list[tuple[str, int, int]] = []   # (name, label, px)
    used: set[int] = set()
    for name, tgt in sorted(targets.items(), key=lambda kv: -kv[1]):
        best, bd = None, 1e18
        for i, a in cands:
            if i in used:
                continue
            d = abs(a / nom_px_per_sf - float(tgt))
            if d < bd:
                bd, best = d, (i, a)
        if best is not None:
            used.add(best[0])
            matched.append((name, best[0], best[1]))

    # Pass 2 — calibrate scale from the matched (px, target) pairs so the whole
    # set best fits the estimate (corrects a slightly-off sheet scale).
    if scale_in_per_ft is None and matched:
        num = sum(px / targets[name] for name, _i, px in matched)
        den = sum((px / targets[name]) ** 2 for name, _i, px in matched)
        k = num / den                       # = 1 / (scale * dpi) ** 2
        scale_in_per_ft = 1.0 / (dpi * (k ** 0.5))
    elif scale_in_per_ft is None:
        scale_in_per_ft = nominal_scale
    px_per_sf = (scale_in_per_ft * dpi) ** 2

    pt_scale = 72.0 / dpi
    page_h, page_w = binary.shape

    zone_list: list[dict] = []
    surfaces = []
    for name, i, a in matched:
        mask = (labels == i).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perim_px = sum(cv2.arcLength(c, True) for c in cnts)

        # extract polygon geometry in PDF points
        polys = []
        for cnt in cnts:
            simplified = cv2.approxPolyDP(cnt, 2.0, True)
            if len(simplified) >= 3:
                polys.append([
                    [float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                    for p in simplified
                ])

        # normalized bounding box
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y_top = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        bbox = [x / page_w, y_top / page_h,
                (x + cw) / page_w, (y_top + ch) / page_h]

        rgb = _SURFACE_COLOR.get(name.upper(), _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb
        area_sf = round(a / px_per_sf, 1)
        perim_lf = round(perim_px * (1.0 / dpi) / scale_in_per_ft, 1)

        surfaces.append({
            "name": name,
            "area_sf": area_sf,
            "perimeter_lf": perim_lf,
            "target_sf": float(targets[name]),
        })

        if not polys:
            # fallback: bounding-box rectangle in PDF points
            polys = [[[int(x * pt_scale), int(y_top * pt_scale)],
                       [int((x + cw) * pt_scale), int(y_top * pt_scale)],
                       [int((x + cw) * pt_scale), int((y_top + ch) * pt_scale)],
                       [int(x * pt_scale), int((y_top + ch) * pt_scale)]]]

        if polys:
            zone_list.append({
                "id": uuid.uuid4().hex[:16],
                "code": name,
                "hex": hex_color,
                "area_sqft": area_sf,
                "perimeter_lf": perim_lf,
                "geometry": polys,
                "bbox": bbox,
                "source": "pool",
                "status": "active",
            })

    # render overlay via shared zones pipeline (consistent with landscape)
    zones_mod.render_from_zones(str(pdf_path), page_idx, zone_list, Path(out_png), dpi=dpi)

    return {
        "surfaces": surfaces,
        "overlay": Path(out_png).name,
        "scale_in_per_ft": scale_in_per_ft,
        "zones": zone_list,
    }
