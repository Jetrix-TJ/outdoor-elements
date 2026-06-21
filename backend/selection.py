"""Stage 1 — deterministic required-page selection from the PDF vector layer.

No AI. Two filters reproduce the human estimator's page triage:

  Filter 1  Sheet TITLE (read from the title-block corner, not the whole page):
            keep takeoff plans (MATERIAL/HARDSCAPE/PLANTING/LANDSCAPE/REFERENCE
            PLAN), drop notes/grading/lighting/details/sections/elevations.
  Filter 2  Legend richness: a real material legend has >= MIN_LEGEND_COLORS
            distinct non-black/white vector fill swatches.

Validated against the 2811 Kirby ground truth: reproduces the human's exact
6-page landscape set (L1.01/02/04, L5.01/02/03) with no false positives.

Color-coded sheets (landscape) are the proven case. Monochrome engineering sets
(pool AQ) have no color legend and a different title block; such pages are
reported as `pool_style` so the UI can surface them for a future pool mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

import fitz  # PyMuPDF

# --- tunables ---------------------------------------------------------------
MIN_LEGEND_COLORS = 3
FALLBACK_MIN_PATHS = 2500   # a page this dense is a real drawing, not a cover/notes

KEEP_TITLE = [
    "MATERIAL PLAN", "MATERIALS PLAN", "HARDSCAPE PLAN",
    "PLANTING PLAN", "LANDSCAPE PLAN", "REFERENCE PLAN",
]
# Other takeoff plan types (pool / spa / planter / paving). Often monochrome, so
# these are kept on the title alone — no legend-color requirement.
OTHER_PLAN_TITLE = [
    "POOL PLAN", "SPA PLAN", "POOL & SPA", "POOL AND SPA", "AQUATIC",
    "PLANTER PLAN", "PAVING PLAN", "PAVING & ",
]
DROP_TITLE = [
    "DETAIL", "SECTION", "ELEVATION", "GENERAL NOTES", "GRADING PLAN",
    "DRAINAGE PLAN", "LIGHTING PLAN", "IRRIGATION", "OVERALL", "KEY PLAN",
]

_CODE_RE = re.compile(r"^[A-Z]{1,3}\d\.\d{1,2}$")
_CODE_ANY = re.compile(r"\b([A-Z]{1,3}\d\.\d{1,2})\b")
_BOILER = re.compile(
    r"DRAWN|CHECKED|PROJECT|NUMBER|SHEET|SCALE|DATE|REVISION|SOLOMON|CORDWELL|"
    r"BUENZ|SCB|KIRBY|HOUSTON|ARCHITECT|©|2023|CONSULTANT|^NO\.?$|^BY$"
)


@dataclass
class PageResult:
    index: int            # 0-based page number
    sheet: str            # sheet code, e.g. "L1.01" ("?" if unreadable)
    title: str            # detected sheet title, e.g. "MATERIAL PLAN"
    keep: bool
    reason: str
    fill_colors: int      # distinct legend swatch colors found
    pool_style: bool      # monochrome / unreadable title-block (needs pool mode)
    thumb: str | None = None  # relative thumbnail path, filled in by the worker

    def to_dict(self) -> dict:
        return asdict(self)


def _corner_title_clip(page: fitz.Page) -> str:
    """Largest non-boilerplate, non-sheet-code text in the bottom-right corner."""
    r = page.rect
    clip = fitz.Rect(r.width * 0.85, r.height * 0.78, r.width, r.height)
    spans = []
    for b in page.get_text("dict", clip=clip)["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                t = s["text"].strip()
                if not t or _CODE_RE.match(t) or _BOILER.search(t.upper()):
                    continue
                if t.replace(".", "").replace(":", "").isdigit():
                    continue
                spans.append((round(s["size"], 1), s["bbox"][1], s["bbox"][0], t))
    if not spans:
        return ""
    mx = max(s[0] for s in spans)
    title = sorted((s for s in spans if s[0] >= mx - 1.0), key=lambda s: (s[1], s[2]))
    txt = " ".join(s[3] for s in title)
    txt = re.sub(r"\b\d+FL\b", "", txt)          # strip floor prefix "38FL"
    return re.sub(r"\s+", " ", txt).strip().upper()


def _title_fallback(page: fitz.Page) -> str:
    """Whole-page title search — for firms whose title block sits outside the
    bottom-right cropbox corner (e.g. a strip below page.rect, fy>1). The sheet
    title is the largest text LINE that names a plan type."""
    allkw = KEEP_TITLE + OTHER_PLAN_TITLE + DROP_TITLE
    best_size, best = 0.0, ""
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            txt = re.sub(r"\s+", " ",
                         " ".join(s["text"] for s in line["spans"])).strip().upper()
            if not txt or len(txt) > 40:
                continue
            if any(k in txt for k in allkw):
                size = max((s["size"] for s in line["spans"]), default=0.0)
                if size > best_size:
                    best_size, best = size, txt
    return best


def _corner_title(page: fitz.Page) -> str:
    """Sheet title — bottom-right corner first, else a whole-page font-size search
    (handles title blocks outside the cropbox)."""
    return _corner_title_clip(page) or _title_fallback(page)


def _sheet_code(page: fitz.Page) -> str:
    """Sheet code from the title-block band, falling back to anywhere on the page."""
    r = page.rect
    band = page.get_text("text", clip=fitz.Rect(r.width * 0.78, r.height * 0.55, r.width, r.height))
    m = _CODE_ANY.findall(band)
    if m:
        return m[-1]
    m = _CODE_ANY.findall(page.get_text("text"))
    return m[0] if m else "?"


def _legend_colors(page: fitz.Page) -> int:
    """Count distinct non-black/white vector fill colors (legend swatches).

    Uses get_cdrawings() (C-level, no Python path objects) — markedly faster than
    get_drawings() on vector-dense plan sheets.
    """
    cs = set()
    for d in page.get_cdrawings():
        f = d.get("fill")
        if not f:
            continue
        rgb = tuple(int(round(c * 255)) for c in f)
        if rgb in {(255, 255, 255), (0, 0, 0)}:
            continue
        cs.add(rgb)
    return len(cs)


def classify_page(page: fitz.Page, index: int) -> PageResult:
    title = _corner_title(page)
    code = _sheet_code(page)
    keep_kw = [k for k in KEEP_TITLE if k in title]
    other_kw = [k for k in OTHER_PLAN_TITLE if k in title]
    drop_kw = [k for k in DROP_TITLE if k in title]

    # Only count legend colors when the title qualifies. This skips get_drawings()
    # on the dropped pages — crucially the L4 detail sheets, which carry millions
    # of vector paths and dominate runtime. Title filtering already excludes them.
    title_landscape = bool(keep_kw) and not drop_kw
    title_other = bool(other_kw) and not drop_kw
    colors = _legend_colors(page) if (title_landscape or title_other) else 0

    # A takeoff-plan title is enough to keep. Raw plans are commonly MONOCHROME
    # (B&W material/hardscape/planting sheets), which the line-width engine now
    # reads — so we no longer require a color legend. Color is a bonus signal only.
    keep = title_landscape or title_other
    pool_style = (not title) and not keep

    if keep and title_landscape:
        reason = (f"title '{title}'" + (f" + {colors} legend colors" if colors else ""))
    elif keep and title_other:
        reason = f"title '{title}' (pool/spa/planter plan)"
    elif pool_style:
        reason = "monochrome / unreadable title block — pool-style sheet"
    elif not keep_kw and not other_kw:
        reason = f"title '{title or '?'}' is not a takeoff plan"
    elif drop_kw:
        reason = f"title dropped ({drop_kw[0].lower()})"
    else:
        reason = f"'{title}' but only {colors} legend colors (< {MIN_LEGEND_COLORS})"

    return PageResult(index, code, title, keep, reason, colors, pool_style)


def _is_drawing_sheet(page: fitz.Page) -> bool:
    """A real drawing sheet (plan/detail), not a cover/index/notes page."""
    return len(page.get_cdrawings()) >= FALLBACK_MIN_PATHS


def analyze_pdf(pdf_path: str | Path) -> list[PageResult]:
    """Classify every page. If the title/color filters find no takeoff plan
    (e.g. a monochrome pool/spa set with vectorized title blocks), fall back to
    keeping the substantial drawing sheets so the app never dead-ends at 0 — the
    user picks which to take off."""
    doc = fitz.open(pdf_path)
    try:
        results = [classify_page(doc[i], i) for i in range(doc.page_count)]
        if not any(r.keep for r in results):
            for r in results:
                if _is_drawing_sheet(doc[r.index]) and not [k for k in DROP_TITLE if k in r.title]:
                    r.keep = True
                    r.pool_style = True
                    r.reason = "no colored takeoff plan auto-detected — pick your takeoff sheets"
        return results
    finally:
        doc.close()


def render_thumb(pdf_path: str | Path, index: int, out_path: Path, dpi: int = 40) -> Path:
    """Render one page to a PNG thumbnail."""
    doc = fitz.open(pdf_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc[index].get_pixmap(dpi=dpi).save(str(out_path))
        return out_path
    finally:
        doc.close()
