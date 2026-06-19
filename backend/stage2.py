"""Stage 2 — detect & color surface-area regions on the raw page (preview only).

Vector-first: read the page's vector fills via PyMuPDF get_drawings(), group the
fills by exact color (conservative — never merges near-identical colors), and
paint each group onto the rendered page as a colored overlay, like the QTO
swatches. A small legend lists each detected color group + its region count.

NO measurement, scale, or QTO comparison here — that's later stages.

Falls back to a light OpenCV color segmentation only when the page has no vector
layer (raster-only), and reports that clearly via `vector=False`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw

# palette assigned per material code on B&W inputs (legend has no colors there)
_LABEL_PALETTE = [
    (255, 193, 7), (33, 150, 243), (76, 175, 80), (233, 30, 99), (156, 39, 176),
    (0, 188, 212), (121, 85, 72), (255, 87, 34), (63, 81, 181), (205, 220, 57),
    (0, 150, 136), (244, 67, 54), (255, 152, 0), (96, 125, 139), (139, 195, 74),
]
_AREA_CODE = re.compile(r"^(M|W)\.\d+[A-Za-z]?$")
# scale notations: 1" = 10'   or   1/8" = 1'-0"
_SCALE_WHOLE = re.compile(r'1\s*["”]\s*=\s*(\d+(?:\.\d+)?)\s*[\'’]')
_SCALE_FRAC = re.compile(r'(\d+)\s*/\s*(\d+)\s*["”]\s*=\s*(\d+)\s*[\'’]')


# a legend row that prints a takeoff value:  (M.5) Concrete Paver A  6,550.15 sq ft
_GT_ROW = re.compile(r"\(([A-Z]+\.?\d+[A-Za-z]?)\)(.*?)([\d,]+\.?\d*)\s*sq\s*ft", re.I | re.S)


def legend_comparison(page: fitz.Page, groups: list[dict]) -> dict | None:
    """Compare our measured areas to the human takeoff printed in the sheet legend.

    Ground truth = each '(CODE) name N sq ft' legend row. We match each code to a
    measured group: by material code (B&W label path) or by the legend swatch
    color (colored path). Returns rows + MAPE, or None when the sheet prints no
    takeoff values (e.g. a raw drawing — upload the QTO to compare).
    """
    txt = page.get_text("text")
    gt = {}
    for code, name, val in _GT_ROW.findall(txt):
        gt.setdefault(code, (re.sub(r"\s+", " ", name).strip(" -:")[:28], float(val.replace(",", ""))))
    if not gt:
        return None

    by_code = {g["label"]: g for g in groups if g.get("label")}
    by_rgb = {tuple(g["rgb"]): g for g in groups if g.get("rgb")}

    # resolve each legend code's swatch fill color (small colored rect left of the code)
    code_color: dict[str, tuple] = {}
    if not by_code:  # colored path → need color→code mapping
        words = page.get_text("words")
        draws = page.get_drawings()
        for w in words:
            t = w[4].strip().strip("()")
            if t not in gt:
                continue
            cy = (w[1] + w[3]) / 2
            xlo, xhi = w[0] - 0.7 * 72, w[0]
            best = None
            for d in draws:
                rgb = _to255(d.get("fill"))
                if rgb is None or rgb == _WHITE:
                    continue
                r = d.get("rect")
                if r is None:
                    continue
                rcx, rcy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                if xlo <= rcx <= xhi and cy - 9 <= rcy <= cy + 9:
                    a = r.width * r.height
                    if best is None or a < best[0]:
                        best = (a, rgb)
            if best:
                code_color[t] = best[1]

    rows = []
    errs = []
    for code, (name, val) in gt.items():
        measured = None
        if code in by_code:
            measured = by_code[code].get("sqft")
        elif code in code_color:
            rgb = code_color[code]
            cand = min(by_rgb, key=lambda k: max(abs(k[i] - rgb[i]) for i in range(3)), default=None)
            if cand is not None and max(abs(cand[i] - rgb[i]) for i in range(3)) <= 12:
                measured = by_rgb[cand].get("sqft")
        err = round((measured - val) / val * 100, 1) if (measured and val) else None
        if err is not None:
            errs.append(abs(err))
        rows.append({"code": code, "name": name, "ground_truth": val,
                     "measured": measured, "error_pct": err})
    rows.sort(key=lambda r: r["ground_truth"], reverse=True)
    mape = round(sum(errs) / len(errs), 1) if errs else None
    return {"rows": rows, "mape": mape, "matched": len(errs)}


def sheet_feet_per_inch(page: fitz.Page, default: float = 10.0) -> float:
    """Read the drawing scale (feet represented by one inch of paper)."""
    txt = page.get_text("text")
    m = _SCALE_WHOLE.search(txt)
    if m:
        return float(m.group(1))
    m = _SCALE_FRAC.search(txt)            # 1/8" = 1'  ->  1" = 8'
    if m:
        num, den, feet = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return feet * den / num
    return default

OVERLAY_DPI = 200
PREVIEW_MAX_W = 2200          # downscale the delivered PNG for snappy web preview
MIN_REGION_PX = 120           # drop speckle (render-pixel area, not a measurement)
MIN_GROUP_PX = 600            # ignore colors with negligible total footprint
MAX_GROUPS = 24
LABEL_DPI = 80               # working resolution for B&W label-seeded coloring
SHADE_THRESHOLD = 245        # include faint gray paver/hatch shades, not just dark ink
_WHITE = (255, 255, 255)

# Debug artifacts (original / vector-lines / final + log) land here so each
# Stage 2 step can be inspected. Toggle off with OE_STAGE2_DEBUG=0.
import os
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEBUG_ON = os.environ.get("OE_STAGE2_DEBUG", "1") != "0"


def _debug_dir(page_index: int) -> Path | None:
    if not DEBUG_ON:
        return None
    d = PROJECT_DIR / "debug" / "stage2" / f"page_{page_index}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dbg_img(dbg: Path | None, name: str, arr) -> None:
    if dbg is None:
        return
    if isinstance(arr, np.ndarray):
        cv2.imwrite(str(dbg / name), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    else:  # PIL
        arr.convert("RGB").save(dbg / name)


def _dbg_log(dbg: Path | None, lines: list[str]) -> None:
    if dbg is None:
        return
    (dbg / "debug.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class ColorGroup:
    rgb: list[int]
    hex: str
    regions: int
    area_px: int              # for ordering only — NOT a real-world measurement
    sqft: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _to255(c):
    return None if c is None else tuple(int(round(v * 255)) for v in c)


def _poly_points(d: dict) -> list[list[tuple[float, float]]]:
    """Filled subpaths of one drawing as point lists (PDF points)."""
    polys, cur = [], []
    for it in d.get("items", []):
        op = it[0]
        if op == "re":
            r = it[1]
            polys.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
        elif op == "qu":
            q = it[1]
            polys.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
        elif op == "l":
            if not cur:
                cur.append((it[1].x, it[1].y))
            cur.append((it[2].x, it[2].y))
        elif op == "c":
            if not cur:
                cur.append((it[1].x, it[1].y))
            cur.append((it[4].x, it[4].y))
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def _shoelace(pts) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])


def _strip_markers(polys: list, sqft_per_pt2: float) -> list:
    """Drop keynote/symbol markers: small, near-square fills stamped at an
    identical footprint many times (material areas don't repeat at one size).
    This is what keeps small materials (M.7/M.15) from being inflated by markers
    of the same fill color."""
    from collections import Counter
    info = []
    foot = Counter()
    for p in polys:
        xs = [q[0] for q in p]
        ys = [q[1] for q in p]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        info.append((p, w, h, _shoelace(p) * sqft_per_pt2))
        foot[(round(w), round(h))] += 1
    out = []
    for p, w, h, a in info:
        near_square = h > 0 and 0.6 <= (w / h) <= 1.7
        if a < 12.0 and near_square and foot[(round(w), round(h))] >= 3:
            continue
        out.append(p)
    return out


def detect_color_regions(pdf_path: str | Path, page_index: int, out_png: Path,
                         dpi: int = OVERLAY_DPI) -> dict:
    """Group vector fills by color and paint a colored overlay on the raw page.

    Returns {vector, message, groups:[ColorGroup...], png}. `groups` is empty
    with a message when there are no colored vector fills to detect.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        scale = dpi / 72.0
        spp2 = scale * scale
        fpi = sheet_feet_per_inch(page)
        sqft_per_pt2 = (fpi / 72.0) ** 2

        dbg = _debug_dir(page_index)

        # FAST routing: render ONCE at a modest DPI (rasterizing this 400k-path
        # page is the costly step) and decide colored-vs-B&W from the pixels.
        wpix = page.get_pixmap(dpi=LABEL_DPI)
        warr = np.frombuffer(wpix.samples, np.uint8).reshape(
            wpix.height, wpix.width, wpix.n)[:, :, :3].copy()
        _dbg_img(dbg, "01_original.png", warr)
        sat = cv2.cvtColor(warr, cv2.COLOR_RGB2HSV)[:, :, 1]
        if float((sat > 60).mean()) <= 0.004:                 # B&W input
            return detect_by_labels(page, out_png, dpi=LABEL_DPI, has_vector=True,
                                    img=warr, dbg=dbg)

        drawings = page.get_drawings()

        # ---- vector-first: group fills by exact color (conservative) ----
        groups: dict[tuple, list[list[tuple[float, float]]]] = {}
        for d in drawings:
            fill = _to255(d.get("fill"))
            if fill is None or fill == _WHITE or min(fill) >= 250:  # skip background/near-white
                continue
            for poly in _poly_points(d):
                if _shoelace(poly) * spp2 >= MIN_REGION_PX:
                    groups.setdefault(fill, []).append(poly)

        chromatic = [c for c in groups if (max(c) - min(c)) > 20]
        if not groups or not chromatic:
            return detect_by_labels(page, out_png, dpi=dpi, has_vector=True)

        # keep chromatic groups + any achromatic ones (e.g. a black-filled material)
        groups = {k: v for k, v in groups.items()
                  if (max(k) - min(k)) > 20 or sum(_shoelace(p) for p in v) * spp2 >= MIN_GROUP_PX}

        # ---- build the colored overlay ----
        base = _render_base(page, dpi)
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)

        summary: list[ColorGroup] = []
        for rgb, polys in groups.items():
            polys = _strip_markers(polys, sqft_per_pt2)
            pt2 = sum(_shoelace(p) for p in polys)
            area_px = int(pt2 * spp2)
            if area_px < MIN_GROUP_PX:
                continue
            r, g, b = rgb
            for poly in polys:
                sp = [(x * scale, y * scale) for x, y in poly]
                odraw.polygon(sp, fill=(r, g, b, 110), outline=(r, g, b, 255))
            summary.append(ColorGroup(list(rgb), _hex(rgb), len(polys), area_px,
                                      round(pt2 * sqft_per_pt2, 1)))

        summary.sort(key=lambda c: c.area_px, reverse=True)
        summary = summary[:MAX_GROUPS]

        # debug: the extracted vector fills on white (what we measured)
        _dbg_img(dbg, "02_vector_fills.png",
                 Image.alpha_composite(Image.new("RGBA", base.size, (255, 255, 255, 255)),
                                       overlay).convert("RGB"))

        img = Image.alpha_composite(base, overlay).convert("RGB")
        if img.width > PREVIEW_MAX_W:
            h = int(img.height * PREVIEW_MAX_W / img.width)
            img = img.resize((PREVIEW_MAX_W, h), Image.LANCZOS)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_png)
        _dbg_img(dbg, "03_final_colored.png", img)

        gdicts = [c.to_dict() for c in summary]
        cmp = legend_comparison(page, gdicts)
        _dbg_log(dbg, [
            f"STAGE 2 — colored page (vector fill grouping)",
            f"page {page_index} | scale 1\"={fpi:.0f}' | dpi {dpi}",
            f"distinct fill colors kept: {len(gdicts)}",
            "", "measured per color group:",
            *[f"  {g['hex']}  {g['sqft']:>10,.1f} sq ft  ({g['regions']} regions)" for g in gdicts],
            "", f"comparison vs human QTO: MAPE {cmp['mape'] if cmp else 'n/a'}%",
            *([f"  {r['code']:5} GT={r['ground_truth']:>9,.1f}  ours={r['measured'] or 0:>9,.1f}  err={r['error_pct']}%"
               for r in cmp["rows"]] if cmp else []),
        ])
        return {"vector": True, "message": "", "png": out_png.name,
                "groups": gdicts, "comparison": cmp}
    finally:
        doc.close()


def _render_base(page: fitz.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")


def _page_is_chromatic(page: fitz.Page) -> bool:
    """Cheap check (low-DPI render): does the page have colored fills, or is it
    black-and-white linework? Routes Stage 2 without parsing the vector layer."""
    pix = page.get_pixmap(dpi=50)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    sat = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[:, :, 1]
    return float((sat > 60).mean()) > 0.004  # >0.4% saturated pixels = a colored drawing


def detect_by_labels(page: fitz.Page, out_png: Path, dpi: int = LABEL_DPI,
                     has_vector: bool = True, img=None, dbg: Path | None = None) -> dict:
    """B&W input: color each material's surface using the M.x/W.x callout labels.

    The raw drawing has no color fills, but it IS labeled — every material region
    has an M.5 / M.6 / ... callout. We read the legend's color per code (palette
    fallback when the legend is B&W), find each label on the plan, and color the
    drawn area nearest each label with that material's color. This reproduces what
    the human did (color by material via the labels), with no human input.

    `img` may be a pre-rendered RGB array at `dpi` to avoid a second rasterization.
    """
    s = dpi / 72.0
    if img is None:
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    pw, ph = page.rect.width, page.rect.height
    fpi = sheet_feet_per_inch(page)
    sqft_per_px = (fpi / dpi) ** 2          # one render-pixel in real sq ft

    in_legend = lambda cx, cy: (0.18 * pw < cx < 0.50 * pw) and (0.38 * ph < cy < 0.78 * ph)
    labels: list[tuple[str, int, int]] = []
    for w in page.get_text("words"):
        t = w[4].strip().strip("()")
        if _AREA_CODE.match(t):
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            if not in_legend(cx, cy):
                labels.append((t, int(cx * s), int(cy * s)))

    base = Image.fromarray(img).convert("RGBA")
    if not labels:
        base.convert("RGB").save(out_png)
        msg = ("No colored fills and no material callout labels found on this page, "
               "so surfaces can't be attributed automatically.")
        return {"vector": has_vector, "message": msg, "groups": [], "png": out_png.name,
                "method": "labels"}

    codes = sorted({t for t, _, _ in labels})
    color = {c: _LABEL_PALETTE[i % len(_LABEL_PALETTE)] for i, c in enumerate(codes)}

    # Capture the faint gray SHADE of the paver/hatch fills, not just dark
    # linework: the material areas render as a light-gray grid (~brightness
    # 175-245) that a "dark-only" threshold throws away. Include it (gray < 245),
    # then close (bridge grid gaps) + open (drop sparse speckle) to solidify each
    # field into a filled region.
    ink = (gray < SHADE_THRESHOLD).astype(np.uint8)
    drawn = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(dpi * 0.18) | 1,) * 2))
    drawn = cv2.morphologyEx(
        drawn, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(dpi * 0.13) | 1,) * 2)).astype(bool)
    drawn[:, int(W * 0.83):] = False
    mm = int(dpi * 0.35)
    drawn[:mm] = drawn[-mm:] = False
    drawn[:, :mm] = drawn[:, -mm:] = False

    # debug: extracted vector linework + the drawn-area mask + the callout labels
    if dbg is not None:
        _dbg_img(dbg, "01_original.png", img)
        viz = np.full((H, W, 3), 255, np.uint8)
        viz[drawn & (ink == 0)] = (225, 238, 255)     # drawn zone (light blue)
        viz[ink > 0] = (40, 40, 40)                   # extracted linework (dark)
        for t, x, y in labels:
            if 0 <= y < H and 0 <= x < W:
                cv2.circle(viz, (x, y), max(4, int(dpi * 0.05)), color[t], -1)
        _dbg_img(dbg, "02_vector_lines.png", viz)

    # nearest-label assignment per material, via distance transforms. With the
    # shade-inclusive mask the fields are already dense, so a moderate cap keeps
    # the fill near its labels (limits bleed into adjacent materials / building).
    maxd = int(dpi * 2.2)
    dists = []
    for c in codes:
        seed = np.full((H, W), 255, np.uint8)
        for t, x, y in labels:
            if t == c and 0 <= y < H and 0 <= x < W:
                seed[y, x] = 0
        dists.append(cv2.distanceTransform((seed > 0).astype(np.uint8), cv2.DIST_L2, 3))
    stack = np.stack(dists, 0)
    nearest = stack.argmin(0)
    mind = stack.min(0)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ov = np.array(overlay)
    label_count: dict[str, int] = {}
    area_px: dict[str, int] = {}
    for ci, c in enumerate(codes):
        region = (nearest == ci) & (mind < maxd) & drawn
        region = cv2.morphologyEx(
            region.astype(np.uint8), cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(dpi * 0.18) | 1,) * 2)).astype(bool)
        if region.sum() < dpi * dpi * 0.6:
            continue
        r, g, b = color[c]
        ov[region] = (r, g, b, 120)
        label_count[c] = sum(1 for t, _, _ in labels if t == c)
        area_px[c] = int(region.sum())

    img_out = Image.alpha_composite(base, Image.fromarray(ov)).convert("RGB")
    if img_out.width > PREVIEW_MAX_W:
        h = int(img_out.height * PREVIEW_MAX_W / img_out.width)
        img_out = img_out.resize((PREVIEW_MAX_W, h), Image.LANCZOS)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img_out.save(out_png)
    _dbg_img(dbg, "03_final_colored.png", img_out)

    groups = [{"rgb": list(color[c]), "hex": _hex(color[c]), "label": c,
               "regions": label_count[c], "area_px": area_px[c],
               "sqft": round(area_px[c] * sqft_per_px, 1)}
              for c in sorted(area_px, key=lambda k: area_px[k], reverse=True)]
    from collections import Counter
    _dbg_log(dbg, [
        "STAGE 2 — B&W input (label-seeded coloring)",
        f"render {W}x{H} @ {dpi} dpi | scale 1\"={fpi:.0f}'",
        f"callout labels found: {len(labels)}  by code: {dict(Counter(t for t, _, _ in labels))}",
        f"materials colored: {len(groups)}",
        "", "measured per material (label-seeded — approximate):",
        *[f"  {g['label']:5} {g['sqft']:>9,.1f} sq ft  ({g['regions']} labels, {g['area_px']} px)"
          for g in groups],
        "", "artifacts: 01_original.png  02_vector_lines.png  03_final_colored.png",
    ])
    msg = (f"B&W input: {len(groups)} materials, boundary-filled from {len(labels)} "
           f"callout labels (scale 1\" = {fpi:.0f}'). The faint paver/hatch fills are "
           f"solidified and flooded from each label — fuller coverage, but boundaries "
           f"between adjacent materials are approximate (no hard walls to separate them).")
    return {"vector": has_vector, "message": msg, "groups": groups,
            "png": out_png.name, "method": "labels",
            "comparison": legend_comparison(page, groups)}
