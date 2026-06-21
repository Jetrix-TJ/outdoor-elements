"""Legend / material-family reader for RAW plans.

A raw takeoff plan tags its areas with material callout codes — Kirby uses `M.5`,
Pelican uses `A2`. Plants (`PL`, botanical `FA`/`QV`), walls (`W`) and reference
notes use other families. The old pipeline hardcoded the `M.x` family, so it only
worked on Kirby.

This module auto-detects the takeoff material family per sheet so detection
generalizes to ANY project's coding. Heuristic: the most frequent single-letter
code family on the sheet is the area-takeoff materials. Verified: Kirby -> `M`
(37 tags), Pelican -> `A` (36-60 tags) — and those are exactly the families each
project's human QTO measures (Kirby M.5/M.6…, Pelican A2/A7…).

    from backend import legend
    fam = legend.detect_material_family(raw_pdf, page)   # {family:'A', codes:['A1'..], ...}
    cfg = legend.tag_config(raw_pdf, page)               # -> tag_pattern + numeric_only
"""
from __future__ import annotations

import collections
import re

import fitz

# A material callout token: optional parens, 1-3 letters, optional . or - sep,
# 1-2 digits.  e.g. "M.5", "A2", "(F-101)" -> family letters, sep, number.
_TAG = re.compile(r"^\(?([A-Za-z]{1,3})([-.]?)(\d{1,2})\)?$")


def tag_families(raw_pdf: str, page: int, clip: dict | None = None) -> dict:
    """Count code tokens by family letter within the (optional) clip region."""
    doc = fitz.open(raw_pdf)
    pg = doc[page]
    W, H = pg.rect.width, pg.rect.height
    words = pg.get_text("words")
    doc.close()

    fam: collections.Counter = collections.Counter()
    sep: dict[str, str] = {}
    codes: dict[str, set] = collections.defaultdict(set)
    for w in words:
        x0, y0 = w[0], w[1]
        if clip:
            fx, fy = x0 / W, y0 / H
            if not (clip.get("left", 0) <= fx <= clip.get("right", 1)
                    and clip.get("top", 0) <= fy <= clip.get("bottom", 1)):
                continue
        m = _TAG.match(w[4].strip())
        if not m:
            continue
        letter = m.group(1).upper()
        fam[letter] += 1
        sep[letter] = m.group(2)
        codes[letter].add(f"{letter}{m.group(2)}{m.group(3)}")
    return {"fam": fam, "sep": sep, "codes": codes}


def detect_material_family(raw_pdf: str, page: int, clip: dict | None = None) -> dict | None:
    """The dominant code family = the takeoff materials. Returns its letter, the
    distinct codes, the separator used, and the tag count (None if no tags)."""
    info = tag_families(raw_pdf, page, clip)
    if not info["fam"]:
        return None
    letter, count = info["fam"].most_common(1)[0]
    return {
        "family": letter,
        "sep": info["sep"].get(letter, ""),
        "codes": sorted(info["codes"][letter], key=_code_key),
        "tag_count": count,
        "all_families": dict(info["fam"].most_common()),
    }


def _code_key(code: str):
    m = re.match(r"([A-Za-z]+)[-.]?(\d+)", code)
    return (m.group(1), int(m.group(2))) if m else (code, 0)


def tag_config(raw_pdf: str, page: int, clip: dict | None = None) -> dict | None:
    """Build the engine tag settings for this sheet's material family — a regex
    that captures the FULL code (e.g. `A2`, `M.5`) so it matches the QTO codes."""
    fam = detect_material_family(raw_pdf, page, clip)
    if not fam:
        return None
    letter = re.escape(fam["family"])
    # accept either separator so "M.5"/"M5"/"M-5" all match
    pattern = rf"^\(?({letter}[-.]?\d{{1,2}})\)?$"
    return {
        "tag_pattern": pattern,
        "tag_numeric_only": False,   # capture the whole code, not just digits
        "family": fam["family"],
        "codes": fam["codes"],
        "tag_count": fam["tag_count"],
        "all_families": fam["all_families"],
    }
