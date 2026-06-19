"""Stage 6 - Gemini validation (Approach B, anti-hallucination layer).

Two jobs on the vector result:
  1. Split shared-color groups: when two materials share an identical fill color
     (e.g. L1.02 Artificial Turf A vs River Rock), color can't separate them, so
     we crop each region and let Gemini attribute it to one of the group's
     materials, then re-sum per material.
  2. Validate the rest: crop each material's largest region and confirm it looks
     like the expected material. Geometry is trusted; disagreements are logged.

No-op without GEMINI_API_KEY (the deterministic combined areas stand, flagged).
"""
from __future__ import annotations

import cv2
import numpy as np

from . import config
from .models import LegendItem


def _crop_region(rgb: np.ndarray, bbox: list[int], pad: int = 10) -> np.ndarray:
    x0, y0, x1, y1 = bbox
    h, w = rgb.shape[:2]
    crop = rgb[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]
    return cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)


def _classify(model, crop_bgr, names: list[str]) -> str:
    ok, buf = cv2.imencode(".png", crop_bgr)
    opts = " | ".join(names)
    prompt = (
        f"This is a cropped fill region from a landscape material plan. Which of "
        f"these materials does it most likely represent? Options: {opts}. "
        f"Answer with exactly one option, verbatim."
    )
    r = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.tobytes()}])
    t = (r.text or "").strip().lower()
    for n in names:
        if n.lower() in t:
            return n
    return names[0]


def _confirm(model, crop_bgr, name: str) -> str:
    ok, buf = cv2.imencode(".png", crop_bgr)
    prompt = (f"A takeoff labels this highlighted fill region as '{name}'. Does it "
              f"plausibly represent that material area? Answer 'yes' or 'no' then a "
              f"short reason under 8 words.")
    r = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.tobytes()}])
    return (r.text or "").strip().replace("\n", " ")


def validate(ctx: dict, seg: dict, calib: dict, items: list[LegendItem]) -> tuple[dict, list[str]]:
    if not config.gemini_api_key():
        return seg, ["Gemini validation: skipped (no GEMINI_API_KEY)"]
    try:
        import google.generativeai as genai

        genai.configure(api_key=config.gemini_api_key())
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        rgb = ctx["rgb"]
        name_by_code = {it.code: it.name for it in items}
        notes: list[str] = []

        # 1) shared-color groups: attribute each region to a member material
        done_groups = set()
        for it in items:
            info = seg.get(it.code) or {}
            shared = info.get("shared_color")
            if not shared:
                continue
            group = tuple(sorted([it.code] + list(shared)))
            if group in done_groups:
                continue
            done_groups.add(group)
            names = [name_by_code[c] for c in group]
            notes.append(f"Gemini split: shared color {group} -> {names}")
            buckets = {c: [] for c in group}
            name_to_code = {name_by_code[c]: c for c in group}
            for reg in info["regions"]:
                pick = _classify(model, _crop_region(rgb, reg.bbox), names)
                buckets[name_to_code.get(pick, group[0])].append(reg)
            for c in group:
                regs = buckets[c]
                seg[c] = {**seg[c], "regions": regs,
                          "sqft": round(sum(r.sqft for r in regs), 2),
                          "note": f"split from shared color via Gemini ({len(regs)} regions)"}
                notes.append(f"  {c} {name_by_code[c]}: {seg[c]['sqft']} sqft ({len(regs)} regions)")

        # 2) confirm the rest
        for it in items:
            info = seg.get(it.code) or {}
            if info.get("shared_color") or not info.get("regions"):
                continue
            biggest = max(info["regions"], key=lambda r: r.pixel_area)
            ans = _confirm(model, _crop_region(rgb, biggest.bbox), it.name)
            verdict = "AGREE" if ans.lower().startswith("y") else "REVIEW"
            notes.append(f"  {it.code} {it.name} [{verdict}]: {ans}")
        return seg, notes
    except Exception as e:  # pragma: no cover
        return seg, [f"Gemini validation: failed ({type(e).__name__}: {e})"]
