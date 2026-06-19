# Outdoor Elements — AI Construction Takeoff (POC)

An AI-assisted **quantity takeoff** tool for landscape / hardscape / pool
construction drawings. You upload an architectural PDF; the app automatically
finds the takeoff sheets, measures each material's surface area (sq ft) and
linear quantities (lf) directly from the drawing's vector geometry, lets a human
review and correct the result, and turns the quantities into a costed estimate.

The whole thing is built as a **staged pipeline** so each step is independently
verifiable against a human estimator's ground truth.

> **Status:** working proof-of-concept. Stage 1 (page selection) and Stage 2
> (surface detection) are validated end-to-end on the `2811 Kirby` project;
> pricing (Stage 3) and a pool/spa vector QTO renderer are in place.

---

## Table of contents

- [What it does](#what-it-does)
- [Why it works (the core idea)](#why-it-works-the-core-idea)
- [Architecture](#architecture)
- [The pipeline](#the-pipeline)
  - [Stage 1 — page selection](#stage-1--page-selection)
  - [Stage 1b — Gemini auto-config](#stage-1b--gemini-auto-config)
  - [Stage 2 — surface detection (3 paths)](#stage-2--surface-detection-three-paths)
  - [Stage 3 — pricing](#stage-3--pricing)
- [Repository layout](#repository-layout)
- [Tech stack](#tech-stack)
- [Data & storage model](#data--storage-model)
- [HTTP API](#http-api)
- [Standalone takeoff CLI (`oe_takeoff`)](#standalone-takeoff-cli-oe_takeoff)
- [Pool & Spa vector QTO renderer (`oe_qto_render`)](#pool--spa-vector-qto-renderer-oe_qto_render)
- [Prototypes](#prototypes)
- [Setup & running](#setup--running)
- [Environment variables](#environment-variables)
- [Results & accuracy](#results--accuracy)
- [Testing](#testing)
- [Roadmap](#roadmap)

---

## What it does

A human estimator doing a takeoff manually:

1. Flips through a 35-page drawing set and keeps only the ~6 sheets that carry
   material plans (drops covers, notes, grading, lighting, details, sections).
2. For each kept sheet, reads the scale and the material legend, then traces
   every colored/hatched zone and computes its area in square feet.
3. Records linear quantities (coping, waterline) and counts (lights, drains).
4. Multiplies quantities by unit rates to produce a priced estimate.

This app reproduces that workflow:

| Step | Human does | App does |
|------|-----------|----------|
| Triage pages | flip & keep | `selection.py` — deterministic 2-filter selector |
| Read scale & legend | OCR by eye | Gemini auto-config + per-sheet scale |
| Measure areas | trace polygons | line-width zone engine / pool mode / color-region |
| Correct mistakes | erase & re-trace | click-to-remove zones, undo, remove-by-material |
| Price | qty × rate | per-job editable rate table |

---

## Why it works (the core idea)

Construction PDFs are **vector CAD**, not scanned images. That's the key: the
drawing already contains exact polygons, line widths, fill colors and text. So
the measurement can be **deterministic and pixel-exact** instead of guessing
from raster pixels.

The headline result on sheet L1.01:

> Raster color-segmentation baseline: **605% MAPE** → exact vector polygons:
> **0.0% MAPE** vs. the human's printed QTO values.

Two reusable insights drive Stage 2:

- **Separate zone boundaries from hatch by vector line *width*** (not pixel
  brightness). Thick paths (≥ 0.35 pt) are zone walls; thin paths are
  hatch/texture. Rasterize the walls only → flood-fill encloses each zone.
- **Anchor zones to the drawing's own callout tags** (`M.5`, `CO-1`, …) when
  present; anchor to **estimate target areas** (e.g. "POOL ~1,109 SF") when the
  sheet has no tags (raw pool plans).

AI (Gemini) is used only where it's genuinely needed — reading the scale/legend
into a machine config, and validating ambiguous fills — never for the actual
measurement.

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │  Browser — React + Vite (port 5173)           │
                    │  upload → kept pages → overlay → edit → price │
                    └───────────────┬──────────────────────────────┘
                                    │  /api  (Vite dev proxy)
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │  FastAPI  (port 8000)  backend/main.py        │
                    │  upload, job status, thumbnails, overlays,    │
                    │  config edit, zone edit, pricing              │
                    └───────┬───────────────────────────┬──────────┘
                            │ dispatch                   │ read/write
                            ▼                            ▼
          ┌──────────────────────────────┐   ┌────────────────────────────┐
          │ Celery tasks  backend/tasks  │   │ Storage                     │
          │  stage1_select  (page triage)│   │  • Postgres (JSONB):        │
          │  stage1_config  (Gemini cfg) │   │      job status, config,    │
          │  stage2_detect  (measure)    │   │      stage-2 results, prices│
          │                              │   │  • Filesystem jobs/<id>/:   │
          │ Broker: Redis                │   │      upload.pdf, thumbs,    │
          │  (or inline "eager" if no    │   │      overlays, mask .npz    │
          │   Redis is running)          │   └────────────────────────────┘
          └───────────┬──────────────────┘
                      │ uses
                      ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ Detection engines                                                   │
   │   selection.py     deterministic page selector (Stage 1)           │
   │   qto_engine.py    line-width zone engine (tagged landscape sheets) │
   │   pool_mode.py     estimate-guided pool/spa surfaces (raw B&W)      │
   │   stage2.py        color-region fallback (already-colored QTO)      │
   │   gemini_config.py Gemini → machine-readable sheet config           │
   │   pricing.py       quantities × unit rates → costed estimate        │
   └───────────────────────────────────────────────────────────────────┘
```

**Graceful degradation is a design principle.** The app stays fully demoable
with zero infrastructure:

- **No Redis?** `celery_app.py` pings Redis at startup; if it's down it flips
  `task_always_eager = True` and tasks run inline in the FastAPI process. Start
  Redis + a worker later and the *same task code* runs distributed.
- **No Postgres?** The API logs a deferred-init warning and retries lazily on
  first use. (Postgres is required for persistence; see setup.)

---

## The pipeline

### Stage 1 — page selection

`backend/selection.py` — **deterministic, no AI.** Two filters reproduce the
estimator's page triage:

1. **Sheet title** read from the bottom-right title-block corner (not the whole
   page). Keep `MATERIAL / HARDSCAPE / PLANTING / LANDSCAPE / REFERENCE PLAN`;
   also keep `POOL / SPA / PLANTER / PAVING / AQUATIC` plans on the title alone.
   Drop `DETAIL / SECTION / ELEVATION / NOTES / GRADING / DRAINAGE / LIGHTING /
   IRRIGATION / OVERALL / KEY PLAN`.
2. **Legend richness** — a real landscape material legend has ≥ 3 distinct
   non-black/white vector fill swatches (uses `get_cdrawings()` — the fast
   C-level path API — and only on title-qualified pages, so it never pays the
   cost of scanning vector-dense detail sheets).

Pool/spa/planter plans are often monochrome with no color legend, so they're
kept on the title alone. If *nothing* qualifies (e.g. a fully monochrome pool
set), Stage 1 falls back to keeping the substantial drawing sheets (≥ 2500
paths) so the app never dead-ends at zero — the user picks.

**Validated:** on `2811 Kirby - LANDSCAPE.pdf` (35 pages) it keeps exactly the
human's set — **L1.01, L1.02, L1.04, L5.01, L5.02, L5.03** — 6/6, no false
positives.

Each page becomes a `PageResult { index, sheet, title, keep, reason,
fill_colors, pool_style, thumb }`. Kept pages get a rendered thumbnail.

### Stage 1b — Gemini auto-config

`backend/gemini_config.py` runs in parallel with Stage 1. It scores pages to
find material-plan candidates, renders up to 12 to JPEG, and asks
**Gemini 3.5 Flash** to emit a machine-readable `qto_config.json`:

- `sheets`: `{ sheet_id: { title, page, scale_in_per_ft } }`
- `tag_pattern` + `tag_numeric_only` (how callouts like `M.5` / `CO-1` look)
- `tag_placement` (`zone_interior` vs `leader_endpoint`) → tunes phase params
- `plan_clip_*_pct` (crop the title block / legend column out of the plan area)
- `zone_detection_codes` (which codes are *area* materials vs point/linear)
- `materials` dict (code → description)

The config is **reviewable**: the UI shows the detected per-sheet scale and lets
the estimator correct it (`PATCH /api/jobs/{id}/config/scale`) before measuring.
If Gemini fails, a fallback empty config is stored with the error surfaced.

### Stage 2 — surface detection (three paths)

`run_stage2()` in `backend/tasks.py` picks one of three engines per page, based
on whether the page is already colored and whether it's a pool plan:

#### Path A — Line-width zone engine (`qto_engine.py`)

For **raw monochrome landscape material plans** with callout tags. The lead's
method, ported faithfully:

1. **Rasterize boundaries only** — paths with width ≥ 0.35 pt, excluding
   parallel-line hatch groups (wood grain, DG, grids). White bg, black walls.
2. **Preprocess** — threshold + dilate to close small gaps so boundaries are
   continuous barriers; the interior becomes fillable white.
3. **Label** all enclosed regions with connected components.
4. **Phase 1 — tag claim:** each material callout tag spiral-searches outward
   from its text position to claim the enclosed component it sits in (with
   per-zone min/max area caps so a small material's tag can't grab a giant
   merged component).
5. **Phase 2 — assign leftovers:** unclaimed fragments are assigned by
   zone-expansion (simple single-code sheets) or by a **distance + hatch-coverage
   score** (complex multi-code sheets). Only tags that actually claimed a Phase-1
   zone act as anchors.
6. **Phase 3b — hatch-CC:** for gravel/DG/turf whose label sits away from the
   zone via a leader arrow, find the nearest hatch pixel and take the connected
   component of the dilated hatch image.
7. **Area** = pixels × `(1/dpi / scale_in_per_ft)²`. A colored overlay PNG is
   blended over the base render.

Per-code zone masks are persisted as a single label image (`masks_p{page}.npz`)
so a later click can identify and remove a region. Results include a
`comparison` (vs the legend's printed values) and a `validation` table (vs known
reference values).

#### Path B — Pool mode (`pool_mode.py`)

For **raw B&W pool/spa plans with no callout tags.** Triggered when the page is a
pool plan and an OE estimate is available. Instead of tags, it anchors on the
*dominant enclosed regions* and matches each to an estimate target by area
(e.g. `POOL ~1,109 SF`, `SPA ~161 SF`):

1. Flood-fill enclosed regions (connected components of the boundary raster).
2. Match each estimate target to the unused region whose area is closest.
3. **Calibrate scale** from the matched (pixels, target-sqft) pairs by
   least-squares, correcting a slightly-off sheet scale so the whole set best
   fits the estimate.
4. Measure area (SF) and perimeter (LF, for coping/waterline) per surface; color
   each surface distinctly (pool = magenta, spa = teal, …).

Estimate targets come from `estimate_parse.py`, which reads the OE estimate PDF
for lines like `(POOL) … 1,109 SF`.

#### Path C — Color-region fallback (`stage2.py`)

For pages that are **already human-colored** (a QTO / colored plan). It extracts
the existing colored surfaces directly rather than running the line-width engine,
and produces the same `groups` / `comparison` shape.

#### Human-in-the-loop correction

After detection, the UI overlays the colored zones on the plan and the estimator
can:

- **Click-to-remove** a single zone (`/stage2/{page}/remove`) — or select
  several and delete in one pass (`/remove_batch`).
- **Remove all of a material** (`/remove_material`).
- **Undo** the last removal (one-level undo backup).

Every edit recomputes areas + masks from the edited label image, re-renders the
overlay, and refreshes the comparison/validation tables.

### Stage 3 — pricing

`backend/pricing.py` turns measured quantities into a costed estimate:
`cost(code) = quantity × unit_rate`. Starter `$/sq ft` rates are seeded by
material-family keyword (tile paver 32, concrete paver 18, wood deck 45, river
rock 9, turf 14, DG 6, …) and are **editable per job** (`PATCH /pricing`). The
result is a table of `{ code, name, qty, unit, rate, cost }` plus a total.

---

## Repository layout

```
outdoor-elements/
├── backend/                    # FastAPI + Celery service
│   ├── main.py                 # FastAPI app & all HTTP endpoints
│   ├── celery_app.py           # Celery (Redis broker; eager fallback)
│   ├── tasks.py                # stage1_select / stage1_config / stage2_detect + edits
│   ├── selection.py            # Stage 1 deterministic page selector
│   ├── gemini_config.py        # Stage 1b Gemini → qto_config.json
│   ├── qto_engine.py           # Stage 2 line-width zone engine (tagged plans)
│   ├── pool_mode.py            # Stage 2 estimate-guided pool/spa surfaces
│   ├── stage2.py               # Stage 2 color-region fallback + legend comparison
│   ├── estimate_parse.py       # parse OE estimate PDF for area targets
│   ├── pricing.py              # quantities × rates → costed estimate
│   ├── db.py                   # SQLAlchemy models (Postgres JSONB)
│   ├── store.py                # job store (Postgres data + filesystem blobs)
│   ├── migrate_jobs.py         # one-shot migration of FS jobs → Postgres
│   ├── schemas.py              # Pydantic response models
│   ├── requirements.txt        # backend deps
│   └── tests/                  # pytest (gemini_config, pricing, qto_engine)
│
├── frontend/                   # React + Vite single-page app
│   ├── src/App.jsx             # upload → pages → overlay → edit → price flow
│   ├── src/api.js              # typed fetch wrappers
│   ├── src/styles.css          # premium-polish UI styling
│   ├── tests/upload.spec.js    # Playwright e2e
│   └── package.json            # react, vite, @playwright/test
│
├── oe_takeoff/                 # standalone end-to-end takeoff CLI (s2–s8)
│   ├── config.py               # paths, scale, ground truth, thresholds
│   ├── s2_extract.py … s8_compare.py
│   └── models.py
│
├── oe_qto_render/              # deterministic vector Pool & Spa QTO renderer
│   ├── model.py  base.py  canvas.py  style.py  format.py
│   └── tests/
│
├── run_takeoff.py              # CLI entry for oe_takeoff (writes outputs/ + RESULTS.md)
├── app.py                      # Streamlit read-only review UI for run_takeoff output
├── sam_proto.py                # prototype: FastSAM region seg prompted by labels
├── tiled_count.py              # prototype: tiled Gemini counting of count-items
├── docker-compose.yml          # Postgres 16 (port 5433)
├── requirements.txt            # engine/CLI deps
├── STAGE1_README.md            # Stage-1 focused doc
├── RESULTS.md                  # measured-vs-ground-truth results
└── docs/                       # design specs, plans, verification screenshots
```

> **Not in the repo** (git-ignored, generated/large): `backend/jobs/` (uploaded
> PDFs + per-job artifacts), `frontend/node_modules`, `frontend/dist`,
> `outputs/`, the `FastSAM-s.pt` model weights, `.redis/`, and `debug/` scratch.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI, Uvicorn |
| Task queue | Celery + Redis (eager fallback when Redis is absent) |
| Database | PostgreSQL 16 (JSONB) via SQLAlchemy 2.0 |
| PDF / vector | PyMuPDF (`fitz`) |
| Computer vision | OpenCV, NumPy, Pillow |
| AI | Google Gemini (`google-generativeai`) — config + validation only |
| Frontend | React 18 + Vite 5 |
| Testing | pytest (backend), Playwright (frontend e2e) |
| Review UI (CLI path) | Streamlit + pandas |

---

## Data & storage model

Split by data type:

- **Postgres (structured JSONB)** — `db.py`:
  - `jobs(job_id, filename, status, config, prices, created_at, updated_at)`
  - `stage2_results(job_id, page, data)` (composite PK)
- **Filesystem** `backend/jobs/<job_id>/` — binaries only:
  - `upload.pdf`, `estimate.pdf`
  - `thumbs/`, `previews/`
  - `stage2/overlay_p{page}.png`, `stage2/masks_p{page}.npz`, `…_undo_…npz`

The `store.py` `read_*/write_*` interface hides this split, so tasks/endpoints
don't care where data lives. `migrate_jobs.py` is a one-shot migration of the
old filesystem-JSON jobs into Postgres.

---

## HTTP API

Base: `http://localhost:8000`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | `{ ok, eager, db }` |
| `POST` | `/api/upload` | multipart `file` (.pdf) → `{ job_id, filename, eager }`; dispatches Stage 1 + config |
| `GET` | `/api/jobs/{id}` | job status + per-page classification |
| `GET` | `/api/jobs/{id}/thumbs/{name}` | page thumbnail PNG |
| `GET` | `/api/jobs/{id}/page/{index}/preview` | high-res page render (cached) |
| `GET` | `/api/jobs/{id}/config` | the Gemini auto-config |
| `PATCH` | `/api/jobs/{id}/config/scale` | correct a sheet's scale |
| `POST` | `/api/jobs/{id}/stage2/{page}` | start Stage-2 detection on a page |
| `GET` | `/api/jobs/{id}/stage2/{page}` | Stage-2 status/result (groups, comparison, validation) |
| `GET` | `/api/jobs/{id}/stage2/{page}/overlay` | colored overlay PNG |
| `POST` | `/api/jobs/{id}/stage2/{page}/pick` | identify the zone at a click (select+confirm) |
| `POST` | `/api/jobs/{id}/stage2/{page}/remove` | remove the clicked zone |
| `POST` | `/api/jobs/{id}/stage2/{page}/remove_batch` | remove multiple clicked zones |
| `POST` | `/api/jobs/{id}/stage2/{page}/remove_material` | remove all zones of one code |
| `POST` | `/api/jobs/{id}/stage2/{page}/undo` | undo the last removal |
| `GET` | `/api/jobs/{id}/pricing?page=` | costed estimate for a page |
| `PATCH` | `/api/jobs/{id}/pricing?page=` | edit a material's unit rate |

CORS is open to the Vite dev server (`localhost:5173`).

---

## Standalone takeoff CLI (`oe_takeoff`)

A self-contained end-to-end takeoff used to develop and validate the method
(mirrors the Grodsky staged structure). Run:

```bash
python run_takeoff.py                 # sheet L1.01 (page 0)
python run_takeoff.py --page 1        # another sheet by 0-based page
python run_takeoff.py --all           # every page with sq-ft legend items
python run_takeoff.py --page 1 --scale 8   # force feet-per-inch if OCR is unsure
```

Pipeline: `s2 extract → s4 calibrate (per-sheet scale) → s3 legend → s5 detect
(vector polygons, markers removed) → s6 Gemini validate/split → s7 annotate →
s8 compare to the legend's printed QTO values`. Outputs land in `./outputs/`
(annotated PNG, JSON/CSV, report) and `RESULTS.md` summarizes the run. Review the
output with `streamlit run app.py`.

---

## Pool & Spa vector QTO renderer (`oe_qto_render`)

A deterministic, **plan-agnostic vector renderer** that produces Pool & Spa QTO
sheets matching a reference sheet (AQ0.0). The **visual style** (colors, symbols,
legend, layout, number formatting) is a locked contract extracted from the
reference PDF's vector objects; all **quantities and geometry** come from each
plan's measured data model — never hardcoded. Output is vector (SVG + vector PDF)
so numbers stay exact. This is the "render" half of the measure/render split.
See `docs/superpowers/specs/2026-06-19-pool-spa-qto-renderer-design.md`.

---

## Prototypes

Exploratory scripts that informed the design (not part of the service):

- **`sam_proto.py`** — FastSAM region segmentation prompted by material-label
  points (an alternative to the vector approach; needs `FastSAM-s.pt`).
- **`tiled_count.py`** — tiled Gemini vision counting of count-items (furniture,
  planters): tile the dense page, run a focused prompt per tile, aggregate,
  compare to the QTO legend.

---

## Setup & running

Prerequisites: **Python 3.11+**, **Node 18+**, **Docker** (for Postgres). Redis
is optional.

### 1. Database (Postgres via Docker)

```bash
docker compose up -d db        # Postgres 16 on host port 5433
```

### 2. Backend

From `outdoor-elements/`:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000
```

Without Redis the API runs in **eager mode** — uploads are processed inline,
fully working.

### 3. Frontend

From `outdoor-elements/frontend/`:

```bash
npm install
npm run dev        # http://localhost:5173  (proxies /api to :8000)
```

### 4. (Optional) Real Celery workers

Start Redis, then:

```bash
celery -A backend.celery_app worker --loglevel=info --pool=solo
```

The same task code now runs distributed; the UI shows "ran on Celery worker". On
Windows, run Redis via Docker (`docker run -p 6379:6379 redis`), Memurai, or WSL.

---

## Environment variables

Put these in a `.env` at the project root (git-ignored):

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | — (required for auto-config) | Google Gemini API key |
| `DATABASE_URL` | `postgresql+psycopg2://oe:oe@localhost:5433/outdoor_elements` | Postgres connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker/backend |
| `OE_PDF_PATH` | a local path | input PDF for the `oe_takeoff` CLI |
| `OE_ESTIMATE_PATH` | — | fallback estimate PDF for pool mode demos |

> The model file `FastSAM-s.pt` and the `backend/jobs/` artifacts are
> intentionally **not** committed. Obtain FastSAM weights separately if you run
> `sam_proto.py`.

---

## Results & accuracy

Sheet **L1.01** (scale 1" = 10', **MAPE 0.0%** vs the human's printed QTO):

| Code | Material | Ground truth (sq ft) | Measured | Error |
|------|----------|---------------------:|---------:|------:|
| M.5 | Concrete Paver A | 6,550.15 | 6,550.11 | −0.0% |
| M.6 | Concrete Paver B | 1,760.05 | 1,760.03 | −0.0% |
| M.7 | Tile Paver A | 191.42 | 191.41 | −0.0% |
| M.15 | River Rock | 368.57 | 368.55 | −0.0% |
| M.16 | Beach Pebble | 413.50 | 413.51 | +0.0% |

> Raster color-segmentation baseline 605% MAPE → exact vector polygons 0.0% MAPE.

Pool mode matches the human estimate to within ~4% via estimate-driven scale
calibration. See `RESULTS.md` and `docs/verification/` for full run logs and
screenshots.

---

## Testing

```bash
# Backend (from outdoor-elements/)
.venv/Scripts/python.exe -m pytest backend/tests oe_qto_render/tests -q

# Frontend e2e (from frontend/)
npx playwright test
```

---

## Roadmap

- [x] Stage 1 — deterministic page selection (validated 6/6 on Kirby)
- [x] Stage 1b — Gemini auto-config (sheets, scale, tags, materials)
- [x] Stage 2 — line-width zone engine for tagged landscape plans (0.0% MAPE)
- [x] Stage 2 — estimate-guided pool/spa mode for raw B&W plans
- [x] Stage 2 — human-in-the-loop correction (click-remove, batch, undo)
- [x] Stage 3 — editable pricing → costed estimate
- [x] Postgres-backed persistence
- [ ] Linear & count quantities surfaced in the web UI (coping/waterline/lights)
- [ ] Pool & Spa vector QTO renderer wired into the web flow
- [ ] Multi-project validation beyond the Kirby set

---

*Outdoor Elements AI takeoff — proof of concept.*
