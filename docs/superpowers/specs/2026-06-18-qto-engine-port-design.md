# Spec: Port the lead's QTO engine + Gemini auto-config into the app

Date: 2026-06-18
Project: Outdoor Elements AI Takeoff POC
Status: Approved (design), pending implementation plan

## Objective

Replace our approximate B&W area-detection (nearest-label / shade-threshold,
~50%+ error on the big paver fields) with the lead's far more accurate method
from `outdoor_qto.py` + `generate_config.py`, integrated into our existing
web app (FastAPI + Redis/Celery + React) without changing the 3-stage UX.

Accuracy target: match the lead's bar — **< 10% delta** per area material vs the
QTO reference (L1.01 M.5≈6,550, M.6≈1,760, M.7≈191; L1.02 M.11≈703, M.12≈1,090,
M.13≈554; L1.04 M.9≈3,937).

## Why this is more accurate (the key insight)

The material zones in the B&W PDF *are* separable — but by vector **line WIDTH**,
not pixel brightness:

- **Zone-boundary walls** are drawn thick (width ≥ ~0.35pt).
- **Hatch / texture** (paver grids, wood grain, gravel stipple) is thin (~0.12pt)
  and often parallel-line groups.

`get_drawings()` exposes a `width` per path, so filtering by width yields a clean
"walls-only" raster. `connectedComponents` then labels each enclosed zone, and
each material callout tag claims the zone it sits in. This is exactly the
"dark vs light" idea done correctly — and it's why the lead's version works where
our pixel-brightness attempts fragmented or merged.

## Architecture — 3 stages, same UX, upgraded engines

```
Upload PDF
  └─ Stage 1: Gemini auto-config  (gemini_config.py, ported from generate_config.py)
        → qto_config.json {sheets, scale/sheet, tag_pattern, tag_placement,
                            zone_detection_codes, materials, clip %, phase params}
        → UI shows detected sheets, scale, materials, area-codes for review
  └─ Stage 2: Detect & measure   (qto_engine.py, ported from outdoor_qto.py)
        per sheet: thick-boundary raster → connected-component zones →
        Phase-1 tag claim + Phase-2 fragment scoring → colored overlay + sq ft
  └─ Stage 3: Compare vs human QTO   (existing legend_comparison + MAPE)
```

The colored-QTO path (vector fill color-grouping, already ~1.9%) stays as-is;
the new engine is used when the sheet is a B&W material plan.

## Components

### `backend/qto_engine.py` (port of `outdoor_qto.py` core, as importable functions)

- `render_thick_boundaries(pdf, page, dpi, min_lw=0.35, min_extent=3.0)` — raster of
  vector paths with `width ≥ min_lw` AND bbox extent ≥ `min_extent`, excluding
  parallel-hatch groups (`_is_parallel_hatch`: ≥4 segments within ~10° and not
  closed). Handles page rotation via `_make_pt_transform`. Returns white image,
  black boundary lines.
- `render_hatch_lines(pdf, page, dpi, max_lw=0.49)` — thin paths only → hatch
  signature image (for Phase-2 coverage scoring).
- `preprocess_for_fill(boundary_img)` — threshold + dilate (close gaps) → binary
  (255 = fillable interior, 0 = barrier).
- `extract_tags(pdf, page, clip, tag_re, numeric_only)` — material callout tags
  from the text layer inside the plan clip; returns `{code, x, y}` (PDF points).
- `detect_zones(binary, tags, pt_to_px, dpi, scale, hatch_dark, phase1_min_zone_sf,
  phase2_radius_ft)` — `connectedComponentsWithStats` once; **Phase 1** per-tag
  spiral claim (skip callout-bubble components < `_CALLOUT_BUBBLE_MAX_PX`);
  **Phase 2** assign unclaimed components — zone-expansion for simple sheets
  (≤1 code), distance + hatch-coverage scoring for complex sheets. Returns
  `{code → mask}`, `{code → px}`.
- `px_to_sqft(px, dpi, scale_in_per_ft)`.
- `run_sheet(pdf, page, sheet_cfg, dpi) -> {code → sqft, code → mask, overlay_png}`
  — orchestrates the above for one sheet using the per-job config.

Ported faithfully (same thresholds/constants) so the lead's tuning carries over.
Overlay rendered with our existing PIL style (not matplotlib) to match the UI.

### `backend/gemini_config.py` (port of `generate_config.py`)

- `score_page` / `find_key_pages` — deterministic page scoring (scale regex, plan
  keywords, code density) to pick candidate material-plan pages.
- `build_config(pdf, api_key) -> dict` — render candidate pages to JPEG, call
  **`gemini-3.5-flash`** (via installed `google.generativeai` SDK) with the lead's
  structured prompt → parse JSON → `finalize_config` defaults. Output is the
  per-job `qto_config.json`.
- Model substitution: lead uses `gemini-3.1-pro-preview` (not on our key);
  use `gemini-3.5-flash`. Keep the prompt verbatim.

### Wiring (`backend/tasks.py`, `backend/main.py`, `backend/store.py`)

- Upload → run `gemini_config.build_config` (Celery task `stage1_config`),
  persist `qto_config.json` in the job dir; status returns the config for review.
- Stage 2 task uses `qto_engine.run_sheet` with the job's config; persists the
  colored overlay + per-code sq ft (same result shape Stage 3 already consumes).
- Frontend Stage 1 view shows the detected config (sheets, scale, materials,
  area-codes); Stage 2/Stage 3 views unchanged in structure.

## Config schema (per job — the lead's schema, unchanged)

```json
{
  "sheets": {"L1.01": {"title": "1FL Material Plan", "scale_in_per_ft": 0.0625, "page": 3}},
  "tag_pattern": "^\\(?M[-.]?(\\d{1,2})\\)?$",
  "tag_numeric_only": true,
  "tag_placement": "zone_interior",
  "plan_clip_bottom_pct": 0.92, "plan_clip_right_pct": 0.80,
  "phase1_min_zone_sf": 0, "phase2_radius_ft": 24,
  "zone_detection_codes": ["M.5","M.6","M.7","M.15","M.16"],
  "materials": {"M.5": "Lucca 4x24 Graphite Paver"}
}
```

## Data flow

1. Upload → `stage1_config` (Gemini) → `qto_config.json` in job dir → Stage 1 review.
2. Stage 2 → `qto_engine.run_sheet(page, config[sheet])` → masks + sq ft + overlay PNG.
3. Stage 3 → `legend_comparison` joins our sq ft to the QTO legend values → MAPE.

## Error handling

- Gemini config failure → fall back to deterministic page selection + default
  config (scale from the sheet's title-block regex, tag pattern `M.x`,
  zone codes = all area codes); surface a "config: fallback" note.
- `run_sheet` failure on a sheet → status `error` with the message (existing pattern).
- Scale: prefer config value; if absent, parse from title-block text; else default.

## Testing / validation

- Unit-ish: run `qto_engine.run_sheet` on L1.01/L1.02/L1.04 of the real Kirby
  LANDSCAPE PDF; assert each area code is within **±10%** of the QTO reference.
- A validation table (computed vs reference vs delta %) like the lead's, surfaced
  in Stage 3 and the debug log.
- Playwright: upload → Stage 1 shows config → Stage 2 renders overlay → Stage 3
  shows comparison.
- Keep writing debug artifacts to `debug/stage2/page_<n>/`.

## Out of scope (this spec)

- Counts (SF.x) and linear (W.x) measurement — area materials only, matching the
  lead's `zone_detection_codes` focus. (Future spec.)
- Pool (AQ) sheets.
- The DWG/DXF exact path (separate track).

## Files touched

- New: `backend/qto_engine.py`, `backend/gemini_config.py`.
- Edit: `backend/tasks.py` (new config task + Stage 2 routing), `backend/main.py`
  (config endpoint/status), `backend/store.py` (config path), frontend Stage 1
  view (show config). `requirements` already satisfied (opencv, matplotlib,
  pymupdf, google-generativeai).
