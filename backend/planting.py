"""Planting-plan takeoff via Gemini 3.1 Pro.

A planting plan's takeoff is a SCHEDULE legend of per-species COUNTS (trees /
palms / shrubs, each) plus AREA items (groundcover / perennial / annual color /
sod, sq ft) — like the human QTO. Plants are labeled individually or in clusters
with a quantity (e.g. "BG 15"), and codes don't map cleanly from text, so we let
gemini-3.1-pro-preview read the sheet and total each code.

    from backend import planting
    counts = planting.count_species(raw_pdf, page)   # {'IC': 44, 'BG': 199, ...}
"""
from __future__ import annotations

import json
import os
import re

import google.generativeai as genai

from . import gemini_config

MODEL = "gemini-3.1-pro-preview"

COUNT_PROMPT = """This is a landscape PLANTING PLAN with a plant SCHEDULE / legend.
Plants are marked on the plan with code labels (e.g. IC, BG, QVE-1, P-FJ, WR).
Some plants are labeled individually; many are CLUSTERS labeled with the code and
a QUANTITY number (e.g. "BG 15" means 15 of plant BG in that cluster).

Count the TOTAL quantity of EACH plant code on this plan — sum the cluster
quantities, and count individually-labeled plants as 1 each.

Return STRICT JSON only, no prose:
[{"code":"<code exactly as written>","count":<integer total>}]"""


SCHEDULE_PROMPT = """This is a landscape PLANT SCHEDULE / legend table mapping plant
CODES to botanical / common names, grouped by category (Canopy Trees, Palm Trees,
Understory Trees, Shrubs, Perennials, Groundcovers, Vines, Annual Color, Sod...).

Return STRICT JSON only, one object per row:
[{"code":"<code exactly as written>","name":"<common name>","category":"<category>"}]
Include EVERY row. JSON only, no prose."""

# categories whose items are COUNTED (each) vs measured by AREA (sq ft)
_COUNT_CATS = ("tree", "palm", "shrub")
_AREA_CATS = ("groundcover", "ground cover", "perennial", "vine", "annual", "sod", "grass")


def find_schedule_page(pdf: str) -> int | None:
    """Page holding the plant schedule (a table with botanical names)."""
    import fitz
    doc = fitz.open(pdf)
    try:
        for i in range(len(doc)):
            up = doc[i].get_text().upper()
            if "SCHEDULE" in up and ("BOTANICAL" in up or "COMMON NAME" in up):
                return i
    finally:
        doc.close()
    return None


def read_schedule(pdf: str, page: int | None = None, api_key: str | None = None,
                  dpi: int = 160) -> list[dict]:
    """Vision-read the plant schedule -> [{code, name, category, unit}]. unit is
    'count' for trees/palms/shrubs, 'area' for groundcover/perennial/sod/etc."""
    if page is None:
        page = find_schedule_page(pdf)
    if page is None:
        return []
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([SCHEDULE_PROMPT, {"mime_type": "image/jpeg", "data": jpeg}])
    return _parse_schedule(resp.text or "")


def _parse_schedule(text: str) -> list[dict]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict) or not d.get("code"):
            continue
        cat = str(d.get("category", "")).lower()
        unit = "count" if any(k in cat for k in _COUNT_CATS) else \
               ("area" if any(k in cat for k in _AREA_CATS) else "count")
        out.append({"code": str(d["code"]).strip().upper(),
                    "name": str(d.get("name", "")).strip(),
                    "category": cat, "unit": unit})
    return out


def _api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("GEMINI_API_KEY")
    if not key:
        from dotenv import load_dotenv
        from . import store
        load_dotenv(store.BACKEND_DIR.parent / ".env")
        key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return key


def _parse_counts(text: str) -> dict[str, int]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[str, int] = {}
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        code = str(d.get("code", "")).strip().upper()
        try:
            n = int(round(float(d.get("count", 0))))
        except (TypeError, ValueError):
            continue
        if code and n > 0:
            out[code] = out.get(code, 0) + n
    return out


def count_species(pdf: str, page: int, api_key: str | None = None,
                  dpi: int = 170, valid_codes: set | None = None) -> dict[str, int]:
    """Total count of each plant code on one planting-plan page (vision). When
    valid_codes is given, only those schedule codes are kept (anchoring)."""
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    prompt = COUNT_PROMPT
    if valid_codes:
        prompt += "\nOnly use these plant codes: " + ", ".join(sorted(valid_codes))
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": jpeg}])
    c = _parse_counts(resp.text or "")
    if valid_codes:
        vc = {v.upper() for v in valid_codes}
        c = {k: n for k, n in c.items() if k in vc}
    return c


import fitz  # noqa: E402

_CODE_TOK = re.compile(r"^[A-Z]{1,4}(?:-[A-Z0-9]{1,3})?$")


def text_label_counts(pdf: str, page: int, valid_codes: set | None = None) -> dict[str, int]:
    """Number of code LABELS per plant code on the page (deterministic). Accurate
    for individually-labeled plants (trees/palms); a floor for dense beds."""
    doc = fitz.open(pdf)
    words = doc[page].get_text("words")
    doc.close()
    vc = {v.upper() for v in valid_codes} if valid_codes else None
    out: dict[str, int] = {}
    for w in words:
        t = w[4].strip().upper()
        if _CODE_TOK.match(t) and (vc is None or t in vc):
            out[t] = out.get(t, 0) + 1
    return out


def planting_count_rows(pdf: str, count_pages: list[int], schedule_page: int | None = None,
                        api_key: str | None = None) -> tuple[list[dict], list[dict]]:
    """Fully-automatic planting count: read the schedule for the anchor codes,
    hybrid-count each on the planting pages, return takeoff rows (unit 'count').
    No QTO needed. Returns (rows, schedule)."""
    sched = read_schedule(pdf, schedule_page, api_key)
    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    names = {s["code"]: s["name"] for s in sched}
    if not count_codes:
        return [], sched
    counts = hybrid_counts(pdf, count_pages, valid_codes=count_codes, api_key=api_key)
    rows = []
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n > 0:
            rows.append({"code": code, "name": names.get(code, code), "unit": "count",
                         "detect": "symbol", "quantity": n, "unit_label": "each",
                         "source": "planting"})
    return rows, sched


def label_positions(pdf: str, page: int, valid_codes: set | None = None) -> dict[str, list]:
    """Plant code label centers (PDF points) per code on the page."""
    doc = fitz.open(pdf)
    words = doc[page].get_text("words")
    doc.close()
    vc = {v.upper() for v in valid_codes} if valid_codes else None
    out: dict[str, list] = {}
    for w in words:
        t = w[4].strip().upper()
        if _CODE_TOK.match(t) and (vc is None or t in vc):
            out.setdefault(t, []).append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
    return out


POINTS_PROMPT = """This is a landscape PLANTING PLAN. Find each PLANT SYMBOL DRAWN on
the plan (the tree/palm/shrub/groundcover symbols in the planting beds, NOT the
text labels) and return its species code and the location of the DRAWN symbol.
Use ONLY these plant codes: {codes}.
Return STRICT JSON only: [{{"code":"<code>","point":[y,x]}}]
point normalized 0-1000 (y top->bottom, x left->right). JSON only."""


def plant_points(pdf: str, page: int, count_codes: set, api_key: str | None = None,
                 dpi: int = 170) -> dict[str, list]:
    """Locations of the DRAWN plant symbols per species (PDF points), via vision —
    so coloring lands on the plants in the beds, not on the edge callout labels."""
    if not count_codes:
        return {}
    key = _api_key(api_key)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(MODEL)
    prompt = POINTS_PROMPT.format(codes=", ".join(sorted(count_codes)))
    jpeg = gemini_config.render_page_jpeg(pdf, page, dpi=dpi)
    resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": jpeg}])
    t = re.sub(r"^```(?:json)?|```$", "", (resp.text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    doc = fitz.open(pdf)
    r = doc[page].rect
    doc.close()
    vc = {c.upper() for c in count_codes}
    out: dict[str, list] = {}
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("code", "")).upper()
        p = it.get("point")
        if c in vc and isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                y, x = float(p[0]), float(p[1])
            except (TypeError, ValueError):
                continue
            out.setdefault(c, []).append((x / 1000 * r.width, y / 1000 * r.height))
    return out


def _species_color(i: int) -> tuple:
    import colorsys
    cr, cg, cb = colorsys.hsv_to_rgb((i * 0.137) % 1.0, 0.62, 0.92)
    return (int(cr * 255), int(cg * 255), int(cb * 255))


def render_planting_overlay(pdf: str, page: int, sched: list[dict], out_path: str,
                            dpi: int = 150, api_key: str | None = None) -> str:
    """Render the plan with each species' plants as filled, semi-transparent COLOR
    PATCHES (so planting beds read as colored, like the human takeoff), plus a
    legend on the right (swatch + code + name + count). Bed positions come from the
    drawn plant symbols (vision points), falling back to label positions."""
    from PIL import Image, ImageDraw
    doc = fitz.open(pdf)
    pg = doc[page]
    pix = pg.get_pixmap(dpi=dpi)
    plan = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGB")
    W, H, pw, ph = pix.width, pix.height, pg.rect.width, pg.rect.height
    doc.close()

    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    names = {s["code"]: s["name"] for s in sched}
    try:
        pos = plant_points(pdf, page, count_codes, api_key=api_key)
    except Exception:  # noqa: BLE001
        pos = {}
    if not pos:
        pos = label_positions(pdf, page, count_codes)

    # paint filled, semi-transparent patches on a separate layer so overlapping
    # plants of one species merge into a colored bed
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    blob = max(16, int(W / 110))
    ordered = sorted(pos, key=lambda c: -len(pos[c]))   # stable color by frequency
    color_of = {code: _species_color(i) for i, code in enumerate(ordered)}
    for code in ordered:
        col = color_of[code]
        for (x, y) in pos[code]:
            cx, cy = x / pw * W, y / ph * H
            dl.ellipse([cx - blob, cy - blob, cx + blob, cy + blob], fill=col + (95,))
            dl.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=col + (220,))
    plan = Image.alpha_composite(plan.convert("RGBA"), layer).convert("RGB")

    # legend panel on the right
    counts = {c: len(p) for c, p in pos.items()}
    LW = max(300, int(W * 0.16))
    canvas = Image.new("RGB", (W + LW, H), "white")
    canvas.paste(plan, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([W, 0, W + LW, H], fill=(250, 250, 251))
    d.line([W, 0, W, H], fill=(220, 220, 224), width=1)
    pad = int(LW * 0.06)
    fz = max(13, int(LW / 26))
    f, fb = _legend_font(fz), _legend_font(int(fz * 1.4), True)
    y = pad
    d.text((W + pad, y), "PLANTS — by species", font=fb, fill=(25, 25, 30)); y += int(fz * 2.2)
    rh = int(fz * 1.9)
    for code in ordered:
        col = color_of[code]
        d.rectangle([W + pad, y + 3, W + pad + fz, y + 3 + fz], fill=col, outline=(140, 140, 140))
        d.text((W + pad + fz + 8, y), f"{code}", font=_legend_font(fz, True), fill=(30, 30, 36))
        d.text((W + pad + fz + 8 + int(LW * 0.18), y), f"{counts[code]}", font=f, fill=(26, 115, 232))
        nm = (names.get(code, "") or "")[:18]
        d.text((W + pad + fz + 8 + int(LW * 0.30), y), nm, font=f, fill=(110, 110, 118))
        y += rh
        if y > H - rh:
            break
    canvas.save(out_path)
    return out_path


def _legend_font(size: int, bold: bool = False):
    from PIL import ImageFont
    for n in (["arialbd.ttf"] if bold else ["arial.ttf"]) + ["DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(n, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def page_count_rows(pdf: str, page: int, sched: list[dict], api_key: str | None = None,
                    min_labels: int = 8) -> list[dict]:
    """Per-species count rows for ONE planting page, given the schedule. Gated on
    a DETERMINISTIC signal (>= min_labels plant labels on the page) so it only
    runs on real planting plans and never depends on a flaky vision tree count."""
    count_codes = {s["code"] for s in sched if s["unit"] == "count"}
    names = {s["code"]: s["name"] for s in sched}
    labels = text_label_counts(pdf, page, count_codes)
    if sum(labels.values()) < min_labels:
        return []   # not a planting page
    try:
        vis = count_species(pdf, page, api_key=api_key, valid_codes=count_codes)
    except Exception:  # noqa: BLE001
        vis = {}
    rows = []
    for code in count_codes:
        n = max(labels.get(code, 0), vis.get(code, 0))
        if n > 0:
            rows.append({"code": code, "name": names.get(code, code), "unit": "count",
                         "detect": "symbol", "quantity": n, "unit_label": "each",
                         "source": "planting"})
    return sorted(rows, key=lambda r: -r["quantity"])


def hybrid_counts(pdf: str, pages: list[int], valid_codes: set | None = None,
                  api_key: str | None = None) -> dict[str, int]:
    """Per-species count over several planting pages: max(label count, vision
    count) per code — labels nail individually-marked plants, vision catches the
    denser beds. Anchored to valid_codes (the schedule) when given."""
    total: dict[str, int] = {}
    for pg in pages:
        labels = text_label_counts(pdf, pg, valid_codes)
        try:
            vis = count_species(pdf, pg, api_key=api_key, valid_codes=valid_codes)
        except Exception:  # noqa: BLE001
            vis = {}
        for code in set(labels) | set(vis):
            total[code] = total.get(code, 0) + max(labels.get(code, 0), vis.get(code, 0))
    return total
