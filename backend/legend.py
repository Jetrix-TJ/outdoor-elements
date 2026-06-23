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


def _norm_code(code: str) -> str:
    """Normalize a material code for cross-source matching: strip wrapping parens
    and spaces, upper-case. '(F-101)' -> 'F-101', ' m.5 ' -> 'M.5'."""
    return (code or "").strip().strip("()").strip().upper()


def area_material_codes(raw_pdf: str, page: int, clip: dict | None = None,
                        materials: dict | None = None) -> list[str] | None:
    """Legend-driven AREA-material callouts present on the sheet — the codes that
    should drive zone (polygon area) detection.

    A code FAMILY counts as AREA when at least one of its codes is named in the
    `materials` legend and that name classifies as an area material (paving, tile,
    turf, concrete, gravel …). Every callout code sharing that family letter is
    then included — so a tile `T.2` rides in on `T.1`'s legend entry even when only
    `T.1` is spelled out in the schedule. Linear families (walls/edging) and count
    families (site furnishings `SF`, benches, lights) are excluded, so they can
    neither be measured as areas nor steal a neighbouring area zone.

    Returns the sorted area codes, or None when no family can be confirmed as area
    (the caller then keeps its existing behaviour — no zone-code filter). This is
    deterministic: it adds no Gemini call.

    Single-family sheets are unaffected: Kirby's only legend family is `M` (paving)
    so the result is exactly the `M.*` codes — same set the engine already used.
    """
    from .material_plan import classify_material

    info = tag_families(raw_pdf, page, clip)
    codes_by_fam: dict[str, set] = info["codes"]
    if not codes_by_fam:
        return None
    mat = { _norm_code(k): v for k, v in (materials or {}).items() }

    area_families: set[str] = set()
    for letter, codeset in codes_by_fam.items():
        for c in codeset:
            name = mat.get(_norm_code(c))
            if name and classify_material(name)["unit"] == "area":
                area_families.add(letter)
                break

    if not area_families:
        return None
    out: list[str] = []
    for letter in area_families:
        out.extend(codes_by_fam[letter])
    return sorted(out, key=_code_key)


def area_tag_pattern(area_codes: list[str]) -> str:
    """A tag regex that fully matches exactly the given codes' families, e.g.
    ['P.1','T.2'] -> r'^\\(?((?:P|T)[-.]?\\d{1,2})\\)?$'. Lets the engine extract
    every area family on a multi-material sheet while ignoring non-area callouts."""
    letters = sorted({_code_key(c)[0] for c in area_codes})
    alt = "|".join(re.escape(l) for l in letters)
    return rf"^\(?((?:{alt})[-.]?\d{{1,2}})\)?$"


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
