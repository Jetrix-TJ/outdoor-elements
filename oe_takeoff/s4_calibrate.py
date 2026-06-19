"""Stage 4 - calibrate pixels-per-foot from the sheet scale, cross-checked
against the printed graphic scale bar.

Primary (deterministic): the sheet states 1" = 10' and the PDF page is true
paper size (48"x36"), so pixels_per_foot = DPI / 10.

Cross-check: the graphic scale bar is labelled 0..20 ft. Its tick labels live
in the PDF text layer, so the 0->20 span in points gives an independent
pixels-per-foot. We log the agreement; we do not hardcode the bar location.
"""
from __future__ import annotations

from . import config


def ppf_from_sheet_scale(feet_per_inch: float, dpi: int = config.RENDER_DPI) -> float:
    return dpi / feet_per_inch


def read_scale_and_sheet_gemini(ctx: dict) -> tuple[float | None, str | None]:
    """OCR the title-block scale ('1" = N'') and sheet number via Gemini vision.

    The title-block text is outlined (not in the PDF text layer), so vision is
    the reliable cross-sheet reader. Returns (feet_per_inch, sheet_number).
    """
    if not config.gemini_api_key():
        return None, None
    try:
        import json as _json
        import re as _re

        import cv2
        import google.generativeai as genai
        import numpy as np

        genai.configure(api_key=config.gemini_api_key())
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        h, w = ctx["rgb"].shape[:2]
        crop = ctx["rgb"][int(0.60 * h):h, int(0.60 * w):w]   # bottom-right title block
        ok, buf = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
        prompt = (
            "From this drawing title-block crop, return JSON "
            '{"feet_per_inch": <number>, "sheet": "<sheet number like L1.01>"}. '
            "feet_per_inch is the feet that 1 inch equals in the scale "
            "(e.g. SCALE: 1\" = 10'-0\" -> 10). Use null if not visible. JSON only."
        )
        r = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.tobytes()}])
        m = _re.search(r"\{.*\}", r.text or "", _re.DOTALL)
        if not m:
            return None, None
        data = _json.loads(m.group(0))
        fpi = data.get("feet_per_inch")
        return (float(fpi) if fpi else None), (data.get("sheet") or None)
    except Exception:
        return None, None


def validate_page_size(page_w_in: float, page_h_in: float, tol_in: float = 0.5) -> bool:
    """Confirm the true-paper-size assumption (else the DPI/10 rule is unsafe)."""
    w, h = sorted([page_w_in, page_h_in])
    ew, eh = sorted([config.EXPECTED_PAGE_W_IN, config.EXPECTED_PAGE_H_IN])
    return abs(w - ew) <= tol_in and abs(h - eh) <= tol_in


def try_scale_bar_ppf(words: list[tuple], ppi: float) -> float | None:
    """Measure pixels-per-foot from the graphic scale bar's text ticks.

    Finds a 'SCALE' token, then the '0' and '20' tick labels on a shared
    horizontal line near it. Their x-span in PDF points * ppi = bar pixel
    length covering 20 ft. Returns None if the ticks can't be located.
    """
    scale_anchor = None
    for w in words:
        if "SCALE" in w[4].upper():
            scale_anchor = w
            break
    if scale_anchor is None:
        return None
    ay = (scale_anchor[1] + scale_anchor[3]) / 2.0

    def ticks(label: str) -> list[tuple]:
        out = []
        for w in words:
            if w[4].strip() == label:
                cy = (w[1] + w[3]) / 2.0
                # tick labels sit just above the SCALE caption, same locale
                if abs(cy - ay) < 60 and abs(w[0] - scale_anchor[0]) < 600:
                    out.append(w)
        return out

    zeros, twenties = ticks("0"), ticks("20")
    if not zeros or not twenties:
        return None
    z = min(zeros, key=lambda w: w[0])
    t = max(twenties, key=lambda w: w[0])
    span_pts = abs((t[0] + t[2]) / 2.0 - (z[0] + z[2]) / 2.0)
    if span_pts <= 0:
        return None
    span_px = span_pts * ppi
    return span_px / 20.0  # 0 -> 20 ft


def calibrate(ctx: dict, feet_per_inch: float | None = None) -> dict:
    dpi = config.RENDER_DPI
    paper_ok = validate_page_size(ctx["page_w_in"], ctx["page_h_in"])

    notes: list[str] = []
    sheet_read = None
    if feet_per_inch is None:
        fpi_g, sheet_read = read_scale_and_sheet_gemini(ctx)
        if fpi_g:
            feet_per_inch = fpi_g
            notes.append(f"Scale read by Gemini: 1\" = {feet_per_inch:.0f}'"
                         + (f"  (sheet {sheet_read})" if sheet_read else ""))
        else:
            feet_per_inch = config.DEFAULT_FEET_PER_INCH
            notes.append(f"Scale not read; using default 1\" = {feet_per_inch:.0f}' "
                         "(set per sheet if wrong)")
    else:
        notes.append(f"Scale (provided): 1\" = {feet_per_inch:.0f}'")

    ppf = ppf_from_sheet_scale(feet_per_inch, dpi)
    bar_ppf = try_scale_bar_ppf(ctx["words"], ctx["ppi"])

    notes.append(f"render {dpi} DPI -> pixels_per_foot = {ppf:.2f}")
    notes.append(f"Page size {ctx['page_w_in']:.1f}x{ctx['page_h_in']:.1f} in "
                 f"(true-paper assumption {'OK' if paper_ok else 'FAILED'})")
    if bar_ppf:
        agree = 100.0 * abs(bar_ppf - ppf) / ppf
        notes.append(f"Scale-bar cross-check: {bar_ppf:.2f} px/ft ({agree:.1f}% from {ppf:.2f})")
    else:
        notes.append("Scale-bar cross-check: tick labels not found")

    return {
        "feet_per_inch": feet_per_inch,
        "pixels_per_foot": ppf,
        "sqft_per_pixel": (1.0 / ppf) ** 2,
        "sqft_per_point2": config.sqft_per_point2(feet_per_inch),
        "paper_ok": paper_ok,
        "scale_bar_ppf": bar_ppf,
        "sheet_read": sheet_read,
        "notes": notes,
    }
