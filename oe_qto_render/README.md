# Pool & Spa QTO Renderer — A‑to‑Z

A deterministic, **plan‑agnostic vector renderer** that produces Pool & Spa QTO
sheets matching the reference sheet **AQ0.0** ("POOL & SPA REFERENCE PLAN").

> Visual style (colors, symbols, legend, layout, number formatting) is **fixed**
> and reused for every plan. All quantities/geometry come from each plan's input
> data — **never hardcoded**. Output is **vector** (SVG + PDF) so numbers stay
> exact.

This document is the full record of the build, start to finish.

---

## A. The task

Read `Pool_QTO_Style_Spec.md` + the reference `2811 KIRBY QTO.pdf`, then build a
renderer that generates Pool & Spa QTO sheets matching the reference exactly.
The renderer is the **"render"** half of the spec's measure→render split; the
AI/CV **measurement** step is separate and out of scope here.

## B. What I explored first

1. **Rendered the reference PDF** (`2811 KIRBY QTO.pdf`, 8 pages) to images. Page
   **3** is the target: sheet **AQ0.0**, "POOL & SPA REFERENCE PLAN".
2. **Discovered AQ0.0 is itself vector** — a light‑gray base‑plan underlay plus
   ~195 vector overlay primitives (fills, strokes, the crimson light‑run fan,
   point dots, a Legend box, green dimension labels). This let me extract the
   **exact** style contract instead of guessing from the spec's approximate hexes.
3. **Read the existing codebase** (`outdoor-elements/backend`): a CV/measurement
   engine (`qto_engine.py`, `stage2.py`, `pool_mode.py`) that outputs **raster**
   overlays. The renderer is a distinct, **vector** concern — a new package.

## C. Key findings from the AQ0.0 vector source

- The pastel pink / sage / pale‑yellow area fills are **saturated colors drawn at
  ~30 % opacity** — identical in the plan and in the legend swatch.
- The legend uses **Times New Roman ~18 pt**; dimension labels use **Verdana 14
  pt** in green `#21D022`.
- All colored overlay lines are **4.0 pt**; point dots are **19.2 pt** circles.
- Title block: `2811 KIRBY`, `2811 KIRBY DR, HOUSTON, TX 77098`,
  `© 2024 Solomon Cordwell Buenz`, Drawn By **KAS** / Checked By **SAP**,
  Project Number **2023005**, Sheet **AQ0.0** (lives on the base plan — not
  redrawn).

## D. Design decisions (asked + confirmed)

| Decision | Choice |
|----------|--------|
| Input model | **Plan‑data JSON** — renderer is a pure function of measured data |
| Sheet scope | **Overlay + legend on the gray base** (title block stays on base) |
| Output | **SVG canonical + vector PDF** |
| Stack / location | **Python module in `outdoor-elements`**, reusing the venv + PyMuPDF |

## E. The locked style contract (exact, from AQ0.0)

| # | Element | Geometry | Color | Opacity | Unit |
|---|---------|----------|-------|---------|------|
| 1 | Coping | linear stroke | `#FF9800` | 1.0 | ft |
| 2 | Total SF | area fill | `#FF00DB` | 0.302 | sq ft |
| 3 | Tanning Ledge | area fill | `#009688` | 0.302 | sq ft |
| 4 | Waterline | linear stroke | `#F0FF00` | 1.0 | ft |
| 5 | Bench | linear stroke | `#00ECFF` | 1.0 | ft |
| 6 | Steps | linear stroke | `#CDDC39` | 1.0 | ft |
| 7 | Toe Tile | linear stroke | `#3F51B5` | 1.0 | ft |
| 8 | Planter | linear stroke | `#FF00DB` | 1.0 | ft |
| 9 | Stone Steppers | area fill | `#FFEB3B` | 0.302 | sq ft |
| 10 | Lights | point circle | `#000000` | 1.0 | count |
| 11 | Drain line | linear stroke | `#FF00DB` | 1.0 | ft |
| 12 | Light run | crimson leaders | `#E91E63` | 1.0 | ft |
| 13 | D Markers | point circle | `#03A9F4` | 1.0 | count |
| 14 | Skimmers | point circle | `#00FFA0` | 1.0 | count |

All of this lives in **`style.py`** — the single source of style truth.

## F. Architecture — package `oe_qto_render/`

```
oe_qto_render/
├── style.py            # the fixed contract: 14 elements, colors, units, layout
├── format.py           # number/unit formatting (2 dp, comma thousands, counts)
├── model.py            # pydantic plan-data schema + validation
├── base.py             # base PDF page -> lightened gray PNG underlay
├── canvas.py           # Canvas abstraction + SvgCanvas + fitz PdfCanvas backends
├── renderer.py         # draws overlay+legend bottom->top; render_svg / render_pdf
├── extract_golden.py   # parse AQ0.0 -> golden plan-data JSON (real data instance)
├── cli.py              # `python -m oe_qto_render render ...`
├── golden/             # 2811_kirby.json + rendered .svg/.pdf
└── tests/              # 23 tests (style, format, model, renderer acceptance)
```

**One draw path, two vector backends.** `renderer.draw()` drives an abstract
`Canvas`; `SvgCanvas` emits SVG and `PdfCanvas` draws on a PyMuPDF page. No
cairo/cairosvg dependency. The base plan is an embedded gray raster; **all
text/numbers/lines stay vector** (satisfies the "crisp text, not a raster image"
gate).

## G. Data model (per‑plan JSON)

```jsonc
{
  "base": { "pdf": "plan.pdf", "page_index": 3, "width": 3455.04,
            "height": 2592.0, "rotation": 0 },
  "elements": {
    "coping":     { "total": 199.92,  "segments": [[[x,y],[x,y], ...]] },
    "total_sf":   { "total": 1109.23, "polygons": [[[x,y], ...]] },
    "lights":     { "total": 12,      "points":   [[x,y], ...] },
    "light_run":  { "total": 658.52,  "origin":   [x,y] },
    "drain_line": { "total": "46.6",  "segments": [...] }      // string keeps 46.6
    // ... all 14 keys, geometry in base-page PDF points
  },
  "dimension_labels": [ { "text": "1 ft", "pos": [x,y], "angle": 0 } ],
  "legend_origin": [2040.83, 988.56]   // optional; auto-placed if omitted
}
```

The renderer renders the provided `total` **verbatim** (a string total preserves
source trailing‑zero behavior, e.g. `46.6`). The **light‑run fan is derived** —
one crimson leader `origin → light` per light point, so `Lights` count always
equals the number of leaders (DRY, structurally guaranteed).

## H. Rendering pipeline (stacking order, bottom → top)

```
gray base underlay
  → Total SF fill (lowest area)
  → other area fills (Tanning Ledge, Stone Steppers)
  → linear strokes (waterline, bench, steps, toe tile, planter, drain)
  → coping outline
  → light-run leaders (crimson fan from the single origin)
  → point dots (lights, D markers, skimmers)
  → green dimension labels
  → Legend box (white rounded, serif "Legend", 14 rows, right-aligned values)
```

## I. How to run

```bash
# 1. (one-off) extract a golden data instance from the reference sheet
python -m oe_qto_render.extract_golden "2811 KIRBY QTO.pdf" \
       oe_qto_render/golden/2811_kirby.json 3

# 2. render any plan-data JSON to SVG (+ optional vector PDF)
python -m oe_qto_render render --data oe_qto_render/golden/2811_kirby.json \
       --out sheet.svg --pdf
```

Use `--no-base` to render the overlay+legend without the base underlay.

## J. Verification

- **23 unit/acceptance tests pass** (`pytest oe_qto_render/tests/`), covering:
  the 14‑element fixed order + locked colors; number formatting
  (`199.92`, `1,109.23`, `46.6`, bare counts); model validation; and the spec §9
  acceptance gate on the emitted SVG (14 legend rows in order, locked colors
  present, Total SF underlies other fills, one leader per light, white rounded
  legend box with border).
- **Visual diff vs the real AQ0.0**: rendered the golden 2811 Kirby data and
  compared to the original sheet. The **legend is a pixel‑faithful reproduction**
  (identical header, order, colors, symbols, right‑aligned values) and the
  overlay (pink Total SF fill, orange coping, crimson light‑run fan, point dots)
  aligns on the plan. Comparison images: `_ref/legend_compare.png`,
  `_ref/compare_full.png`.

## K. Scope & limitations

- **In scope:** the deterministic vector overlay + legend, driven entirely by the
  per‑plan data model; SVG + vector PDF output.
- **Out of scope:** feature detection / measurement (the AI/CV step); the base
  linework is embedded as a gray raster (not re‑vectorized); the right‑margin
  DESIGN DATA / POOL NOTES / DRAWING INDEX tables and title block live on the
  base sheet and are not redrawn.
- **Golden caveat:** Planter and Drain line share `#FF00DB`; the golden extractor
  splits those strokes by a length‑fraction heuristic (legend totals are the
  authoritative reference values, so legend output is exact regardless).

## L. File inventory (added this build)

- `oe_qto_render/` — the renderer package (modules listed in §F).
- `oe_qto_render/golden/2811_kirby.{json,svg,pdf}` — golden data + outputs.
- `oe_qto_render/README.md` — this document.
