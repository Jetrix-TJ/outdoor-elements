# Stage 1 — Required-Page Selection (Design)

Date: 2026-06-17
Project: Outdoor Elements AI Takeoff POC
Status: Approved, building

## Goal

Automate the first thing a human estimator does with a client drawing set: out of
a large multi-page PDF (35–47 pages), keep only the **few takeoff plan sheets**
that carry measurable quantities, and drop everything else (covers, notes, grading,
lighting, details, sections, elevations, schedules).

End-to-end POC goal (later stages): measure square footage per material. This spec
covers **Stage 1 only**: upload → select required pages → review → Continue.

## The selection rule (deterministic, no AI)

The input PDFs are true vector CAD exports, so selection reads the vector/text
layer directly. Two filters, applied per page:

**Filter 1 — Sheet TITLE (read from the title-block region, bottom-right ~18% of
the sheet, NOT the whole page).** Reading the whole page is polluted by cross-
reference callouts ("SEE MATERIAL PLAN", "SEE DETAIL").

- KEEP if title contains: `MATERIAL PLAN`, `HARDSCAPE PLAN`, `PLANTING PLAN`,
  `LANDSCAPE PLAN`, or pool `REFERENCE PLAN`.
- DROP if title contains: `GENERAL NOTES`, `OVERALL`, `KEY PLAN`, `GRADING PLAN`,
  `DRAINAGE PLAN`, `LIGHTING PLAN`, `IRRIGATION`, `DETAIL(S)`, `SECTION(S)`,
  `ELEVATION(S)`, `SCHEDULE` (as the primary title).

**Filter 2 — Legend richness.** A page can pass Filter 1 but have nothing to take
off (e.g. L1.03 is a MATERIAL PLAN with no real legend). Keep only pages with a
material/quantity legend: **≥ 3 distinct non-black/white vector fill colors**
(legend swatches) on the sheet.

**KEEP = Filter 1 passes AND Filter 2 passes.**

### Validation against ground truth (2811 Kirby LANDSCAPE, 35 pages)

This rule reproduces the human's exact kept set: L1.01, L1.02, L1.04, L5.01,
L5.02, L5.03 — and drops the other 29 (incl. L1.03 via Filter 2, L6.x lighting
via Filter 1). Pool set adds AQ0.0 (Reference Plan) via the same rule.

## Architecture

```
Frontend (React/Vite)            Backend (FastAPI)            Worker (Celery)
  upload PDF  ───────────────►  POST /api/upload  ──────►  stage1_select task
  poll status ◄──────────────  GET  /api/jobs/{id}         (Redis broker)
  show kept pages grid                                      writes result JSON
  [Continue] → (Stage 2 later)  GET  .../thumb/{n}.png      + page thumbnails
```

- **Backend**: FastAPI. `POST /api/upload` stores the PDF under `jobs/<id>/`,
  dispatches the Stage-1 task, returns `{job_id}`. `GET /api/jobs/<id>` returns
  status + result. Thumbnails served as static files.
- **Worker**: Celery task `stage1_select(job_id)` runs the two-filter funnel over
  every page, renders a thumbnail per kept page, writes `result.json`, sets status.
- **Redis/Celery**: Redis is the broker. If Redis is unreachable, the app falls
  back to Celery **eager mode** (runs the identical task inline) so Stage 1 is
  demoable without Redis; starting Redis + a worker switches to real distribution
  with no code change.
- **Job store**: filesystem (`jobs/<id>/upload.pdf`, `result.json`, `thumbs/`),
  so status survives without a Redis result backend.

## Data shapes

`result.json`:
```json
{
  "job_id": "ab12…", "filename": "2811 Kirby - LANDSCAPE.pdf",
  "status": "done", "page_count": 35, "kept_count": 6,
  "pages": [
    {"index": 2, "sheet": "L1.01", "title": "MATERIAL PLAN",
     "keep": true, "reason": "title=MATERIAL PLAN; 8 legend colors",
     "fill_colors": 8, "thumb": "thumbs/p2.png"},
    {"index": 0, "sheet": "L0.00", "title": "GENERAL NOTES",
     "keep": false, "reason": "title dropped (NOTES)", "fill_colors": 1, "thumb": null}
  ]
}
```

Pydantic models back the API responses.

## Out of scope (later stages)

- Vector line extraction on kept pages (Stage 2).
- Square-footage measurement from lines (Stage 3).
- Gemini validation, multi-sheet QTO assembly.

## Testing

- Run Stage 1 on the real `2811 Kirby - LANDSCAPE.pdf` and assert kept set ==
  {L1.01, L1.02, L1.04, L5.01, L5.02, L5.03}.
- Run on `2811 Kirby - POOL.pdf` and assert AQ0.0 is kept.
