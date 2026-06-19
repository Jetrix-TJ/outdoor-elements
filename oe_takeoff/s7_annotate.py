"""Stage 7 - annotate the sheet with detected regions + labels (any sheet)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import config
from .models import LegendItem, TakeoffRow

# Distinct outline palette (BGR), cycled across however many materials a sheet has.
_PALETTE = [
    (0, 140, 255), (255, 0, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255),
    (0, 200, 0), (180, 105, 255), (0, 215, 255), (128, 0, 128), (0, 128, 255),
    (200, 200, 0), (128, 128, 0),
]


def annotate(ctx: dict, seg: dict, rows: list[TakeoffRow], items: list[LegendItem],
             out_name: str) -> Path:
    rgb = ctx["rgb"]
    canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    overlay = canvas.copy()
    rows_by_code = {r.code: r for r in rows}
    color_by_code = {it.code: _PALETTE[i % len(_PALETTE)] for i, it in enumerate(items)}

    for it in items:
        info = seg.get(it.code)
        if not info or not info.get("regions"):
            continue
        color = color_by_code[it.code]
        polys = [r.contour for r in info["regions"] if r.contour]
        if polys:
            cnts = [np.array(p, dtype=np.int32).reshape(-1, 1, 2) for p in polys]
            cv2.fillPoly(overlay, cnts, color)
            cv2.drawContours(canvas, cnts, -1, color, 3)
        elif info.get("mask") is not None:
            mask = info["mask"].astype(np.uint8)
            overlay[mask.astype(bool)] = color
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(canvas, contours, -1, color, 3)

    canvas = cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0)

    for it in items:
        info = seg.get(it.code)
        if not info or not info.get("regions"):
            continue
        color = color_by_code[it.code]
        biggest = max(info["regions"], key=lambda r: r.pixel_area)
        x0, y0, x1, y1 = biggest.bbox
        row = rows_by_code.get(it.code)
        sqft = row.measured_sqft if row else info["sqft"]
        text = f"{it.code} {it.name}: {sqft:.0f} sqft"
        org = (x0, max(20, y0 - 8))
        font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2
        (tw, tht), _ = cv2.getTextSize(text, font, fs, th)
        cv2.rectangle(canvas, (org[0] - 2, org[1] - tht - 4),
                      (org[0] + tw + 2, org[1] + 4), (255, 255, 255), -1)
        cv2.putText(canvas, text, org, font, fs, color, th, cv2.LINE_AA)

    out = config.OUTPUT_DIR / out_name
    h, w = canvas.shape[:2]
    if w > 4000:
        scale = 4000 / w
        canvas = cv2.resize(canvas, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), canvas)
    return out
