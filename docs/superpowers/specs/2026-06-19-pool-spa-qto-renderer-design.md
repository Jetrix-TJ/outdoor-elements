# Pool & Spa QTO Renderer — Design

**Date:** 2026-06-19
**Status:** Approved → implementing

## Goal

A deterministic, **plan-agnostic vector renderer** that produces Pool & Spa QTO
sheets matching the reference sheet **AQ0.0** ("POOL & SPA REFERENCE PLAN", page 3
of `2811 KIRBY QTO.pdf`).

- The **visual style** (colors, symbols, legend, layout, number formatting) is
  fixed and reused for every plan.
- All **quantities and geometry** come from each plan's input data — never
  hardcoded.
- Output is **vector** (SVG canonical + vector PDF) so numbers stay exact.

This is the "render" half of the spec's measure/render split. Measurement/CV is
out of scope; the renderer consumes an already-measured data model.

## Locked style contract (extracted from the AQ0.0 vector source)

Exact values pulled from the reference PDF's vector drawing objects (these
supersede the approximate hexes in `Pool_QTO_Style_Spec.md`).

| # | Element        | Geometry        | Color     | Opacity | Unit   |
|---|----------------|-----------------|-----------|---------|--------|
| 1 | Coping         | linear stroke   | `#FF9800` | 1.0     | ft     |
| 2 | Total SF       | area fill       | `#FF00DB` | 0.302   | sq ft  |
| 3 | Tanning Ledge  | area fill       | `#009688` | 0.302   | sq ft  |
| 4 | Waterline      | linear stroke   | `#F0FF00` | 1.0     | ft     |
| 5 | Bench          | linear stroke   | `#00ECFF` | 1.0     | ft     |
| 6 | Steps          | linear stroke   | `#CDDC39` | 1.0     | ft     |
| 7 | Toe Tile       | linear stroke   | `#3F51B5` | 1.0     | ft     |
| 8 | Planter        | linear stroke   | `#FF00DB` | 1.0     | ft     |
| 9 | Stone Steppers | area fill       | `#FFEB3B` | 0.302   | sq ft  |
| 10| Lights         | point circle    | `#000000` | 1.0     | count  |
| 11| Drain line     | linear stroke   | `#FF00DB` | 1.0     | ft     |
| 12| Light run      | crimson leaders | `#E91E63` | 1.0     | ft     |
| 13| D Markers      | point circle    | `#03A9F4` | 1.0     | count  |
| 14| Skimmers       | point circle    | `#00FFA0` | 1.0     | count  |

Other locked constants:

- **Linear overlay stroke width:** 4.0 pt (uniform).
- **Area fills:** drawn at opacity 0.302 (the pastel appearance is the saturated
  color at low opacity — identical in plan and in the legend swatch).
- **Point dots:** filled circles, 19.2 pt diameter (r = 9.6 pt), no stroke.
- **Dimension labels:** green `#21D022`, **Verdana 14 pt**, with a 3.0 pt green
  leader and a small green dot; text carries a `ft` suffix.
- **Legend:** white rounded box, thin near-black border (~2.59 pt). Header word
  `Legend`, centered, **Times New Roman ~18 pt**. One row per element in the
  fixed order above. Symbol (diagonal line / filled square / filled circle) at
  left, element name left-aligned, value right-aligned, **no dot leaders**. Row
  pitch ≈ 32.4 pt; legend swatch line weight ≈ 3.89 pt.

## Number & unit formatting (strict)

- Measured values: 2 decimals, comma thousands (`1,109.23`, `199.92`).
- Trailing-zero behavior matches the source: a value like `46.6` keeps one
  decimal when the source data provides it that way. Implementation: format the
  numeric value the data carries; if the datum is given as a string keep it
  verbatim, otherwise format to 2 decimals. (The golden data encodes `46.6` as a
  string to reproduce the reference exactly.)
- Unit suffix after one space: `ft` (linear) / `sq ft` (area).
- Counts: bare integer, no unit.

## Architecture — package `outdoor-elements/oe_qto_render/`

- **`style.py`** — the entire fixed contract: the ordered 14-element table
  (color, geometry type, unit, opacity), stroke widths, point radius, dimension
  label style, legend layout constants, fonts. The single source of style truth;
  plan-agnostic.
- **`format.py`** — pure number/unit formatting functions.
- **`model.py`** — pydantic v2 schema for the plan-data JSON. Per element:
  geometry in PDF-point coordinates plus the precomputed total. Plus
  `light_run.origin`, `dimension_labels`, and a `base` block (PDF path, page
  index, page size, rotation). The renderer renders the provided totals verbatim.
- **`canvas.py`** — a tiny `Canvas` interface (`image`, `polygon`, `polyline`,
  `line`, `circle`, `rounded_rect`, `text`) with two dependency-free backends:
  - `SvgCanvas` → emits an SVG string.
  - `PdfCanvas` → draws onto a fitz/PyMuPDF page (vector).
  Both keep text and geometry vector; the base plan is an embedded raster image.
- **`renderer.py`** — orchestrator. Builds the gray base underlay, then draws
  the overlay layers bottom→top in the spec's stacking order:
  base → Total SF fill → other area fills → linear strokes → coping outline →
  light-run leaders → point dots → green dimension labels → legend box.
  Drives a `Canvas`, so SVG and PDF come from one draw path.
- **`base.py`** — renders the base plan PDF page to a high-DPI raster,
  desaturates + lightens to gray, returns a PNG data URI / image for embedding.
- **`extract_golden.py`** — one-off tool: parses AQ0.0 page 3 vector into
  `golden/2811_kirby.json` (real geometry + true totals + light-run origin),
  giving a genuine data instance and a visual-diff target.
- **`cli.py`** — `python -m oe_qto_render render --data plan.json --out sheet.svg
  [--pdf]`.

## Base underlay strategy

The base pool plan is large vector linework. Matching how AQ0.0 is built, the
renderer rasterizes the base page at high DPI, desaturates + lightens to gray,
and embeds it as the bottom image layer. All overlay content — fills, lines,
dots, and **all text/numbers/the legend** — stays vector, satisfying the "crisp
text, not a raster image" acceptance gate.

## Light-run system (DRY)

The data carries `light_run.origin` and the `lights` point list. The renderer
derives one crimson leader `origin → light` per light (the fan) and renders the
provided `total_ft`. `Lights` count == number of leaders by construction.

## Coordinate system

All geometry is in base-page PDF points (origin top-left, y down), matching SVG
and fitz page space, so the overlay aligns with the embedded base. Page rotation
is taken from the base block (golden page is rotation 0).

## Testing (TDD)

- `format.py`: golden formatting cases (`199.92`, `1,109.23`, `46.6`, counts).
- `style.py`: asserts exactly 14 elements in the fixed order, each with a locked
  color / geometry type / unit / opacity.
- `model.py`: golden JSON loads and validates; rejects unknown elements.
- `renderer.py`: render the golden 2811 Kirby JSON and assert the §9 acceptance
  checklist programmatically against the emitted SVG (14 legend rows in order,
  locked colors present, Total SF fill is the lowest area layer, one leader per
  light, count == leaders, correct number formatting). Plus a generated
  side-by-side PNG vs. the real AQ0.0 crop for human review.

## Scope

Renderer draws the **color overlay + legend on the gray base**. Title block and
right-margin note tables remain part of the base plan (not redrawn). Output:
SVG (canonical) + vector PDF.

## Out of scope

- Feature detection / measurement (the AI/CV step) — the renderer consumes a
  measured data model.
- Reproducing the base linework as vector (it is embedded as a gray raster).
- The right-margin DESIGN DATA / POOL NOTES / DRAWING INDEX tables and title
  block (they live on the base sheet).
