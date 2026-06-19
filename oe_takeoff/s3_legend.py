"""Stage 3 - read the legend (any sheet).

Deterministic path (no key): use the PDF text layer to find every AREA legend
row (a line containing 'sq ft'), parse its code / name / printed value, sample
its swatch color from the rendered pixels, and resolve the material's true
vector fill color via the swatch rectangle. The printed value is the human
takeoff (QTO) and serves as the per-sheet ground truth.

Validation path (Gemini, optional): read the legend crop with vision and
cross-check codes/names against the deterministic result.
"""
from __future__ import annotations

import json
import re

import numpy as np

from . import config
from .models import LegendItem

# Codes like M.5, M.15, W.5B, SF.2A, PL.8 ...
_CODE_RE = re.compile(r"^\(([A-Z]+\.?\d+[A-Za-z]?)\)$")
_VALUE_RE = re.compile(r"([\d,]+\.?\d*)\s*sq\s*ft", re.IGNORECASE)


def _line_words(words: list[tuple], anchor: tuple) -> list[tuple]:
    cy = (anchor[1] + anchor[3]) / 2.0
    return sorted((t for t in words if abs((t[1] + t[3]) / 2.0 - cy) < 6 and t[0] >= anchor[0]),
                  key=lambda t: t[0])


def _sample_swatch(rgb: np.ndarray, code_word: tuple, ppi: float) -> tuple[int, int, int]:
    x0, y0, x1, y1 = code_word[0], code_word[1], code_word[2], code_word[3]
    cy = (y0 + y1) / 2.0
    sx = x0 - 0.30 * 72.0
    px_x, px_y = int(sx * ppi), int(cy * ppi)
    h, w = rgb.shape[:2]
    px_x = max(7, min(w - 8, px_x))
    px_y = max(7, min(h - 8, px_y))
    patch = rgb[px_y - 6:px_y + 6, px_x - 6:px_x + 6].reshape(-1, 3)
    return tuple(int(v) for v in np.median(patch, axis=0))


def _to255(c) -> tuple[int, int, int] | None:
    return None if c is None else tuple(int(round(v * 255)) for v in c)


def _resolve_fill(drawings: list, code_word: tuple) -> tuple[int, int, int] | None:
    """Smallest filled vector rect near the swatch (left of the code text).

    Robust to layout shifts: scans a small box to the left of the code rather
    than requiring an exact point hit, and ignores white.
    """
    cy = (code_word[1] + code_word[3]) / 2.0
    # search window: from ~0.6" left of code to the code's left edge
    x_lo, x_hi = code_word[0] - 0.65 * 72, code_word[0]
    y_lo, y_hi = cy - 9, cy + 9
    hits = []
    for d in drawings:
        if d.get("fill") is None:
            continue
        f = _to255(d["fill"])
        if f in config.VECTOR_IGNORE_COLORS:
            continue
        r = d.get("rect")
        if r is None:
            continue
        rcx, rcy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
        if x_lo <= rcx <= x_hi and y_lo <= rcy <= y_hi:
            hits.append((r.width * r.height, f))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def read_legend_deterministic(ctx: dict) -> list[LegendItem]:
    rgb, words, ppi = ctx["rgb"], ctx["words"], ctx["ppi"]
    drawings = ctx.get("drawings", [])
    items: list[LegendItem] = []
    seen = set()
    for w in words:
        m = _CODE_RE.match(w[4].strip())
        if not m:
            continue
        code = m.group(1)
        line = _line_words(words, w)
        txt = " ".join(t[4] for t in line)
        vm = _VALUE_RE.search(txt)
        if not vm:
            continue                       # not an area (sq ft) row
        if code in seen:
            continue
        seen.add(code)
        name = txt[len(w[4]):vm.start()].strip(" -:")
        value = float(vm.group(1).replace(",", ""))
        items.append(LegendItem(
            code=code, name=name or code, unit="area_sqft",
            swatch_rgb=_sample_swatch(rgb, w, ppi),
            fill_rgb=_resolve_fill(drawings, w),
            legend_value_sqft=value,
        ))
    return items


def validate_with_gemini(ctx: dict, items: list[LegendItem]) -> list[str]:
    if not config.gemini_api_key():
        return ["Legend Gemini cross-check: skipped (no GEMINI_API_KEY)"]
    try:
        import cv2
        import google.generativeai as genai

        genai.configure(api_key=config.gemini_api_key())
        h, w = ctx["rgb"].shape[:2]
        crop = ctx["rgb"][int(0.10 * h):int(0.90 * h), int(0.20 * w):int(0.48 * w)]
        ok, buf = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
        prompt = (
            "This is a legend from a construction material plan. List every row as "
            "JSON array of {code, name, unit} where code looks like 'M.5'. unit is "
            "'area_sqft' if the row shows 'sq ft', else 'other'. Return ONLY JSON."
        )
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        r = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.tobytes()}])
        mm = re.search(r"\[.*\]", r.text or "", re.DOTALL)
        parsed = json.loads(mm.group(0)) if mm else []
        seen = {p.get("code"): p for p in parsed}
        notes = [f"Legend Gemini cross-check: read {len(parsed)} rows"]
        for it in items:
            g = seen.get(it.code)
            if g is None:
                notes.append(f"  ! {it.code} not found by Gemini")
            else:
                notes.append(f"  ok {it.code} '{g.get('name')}' [{g.get('unit')}]")
        return notes
    except Exception as e:  # pragma: no cover
        return [f"Legend Gemini cross-check: failed ({type(e).__name__}: {e})"]


def read_legend(ctx: dict) -> tuple[list[LegendItem], list[str]]:
    items = read_legend_deterministic(ctx)
    notes = [f"Legend deterministic: {len(items)} area items "
             f"({', '.join(i.code for i in items)})"]
    for it in items:
        notes.append(f"  {it.code} {it.name}: swatch {it.swatch_rgb}, "
                     f"vector fill {it.fill_rgb}, legend={it.legend_value_sqft}")
    notes += validate_with_gemini(ctx, items)
    return items, notes
