# Per-Zone ID Storage — Design

**Date:** 2026-06-19
**Status:** Approved → implementing

## Problem

Today zones are **not individually addressable**. Per-page Stage-2 results are a
single JSONB blob (`stage2_results.data` with an aggregate `groups` list), and
the real geometry is a per-pixel label image on disk (`masks_p{page}.npz`).
Selection/deletion is **spatial by click** (find the connected component at a
pixel, zero it). A zone has no stable identity between edits.

## Goal

Store each detected zone as a first-class DB row with a stable **id**, so the UI
can list zones, select one, and **delete it by id** (with restore).

## Decisions (confirmed)

- **Granularity:** one connected region = one zone (one id).
- **Delete:** soft delete (`status='deleted'`) + restore endpoint.
- **Geometry source of truth:** the zone's polygon, stored in the DB row.
- **Scope:** backend + frontend together.

## Schema — new `zones` table

```
zones(
  id            str PK,             -- uuid hex (the stable zone id)
  job_id        str FK -> jobs,
  page          int,
  code          str,                -- material/element code or surface name
  hex           str,                -- display color "#rrggbb"
  area_sqft     float,
  perimeter_lf  float | null,
  geometry      JSONB,              -- [[ [x,y], ... ], ...] polygons in PDF points
  bbox          JSONB,              -- [x0,y0,x1,y1] fractional 0..1
  source        str,                -- engine | pool | color | labels
  status        str,                -- 'active' | 'deleted'
  created_at, updated_at
)  index (job_id, page, status)
```

The label `.npz` is retained for initial extraction; the DB is the source of
truth for rendering and edits.

## Zone lifecycle

1. **Detect** (`run_stage2`): after a page is detected, extract connected
   components per code from the label mask → one zone row each (contour polygon
   in PDF points, area, perimeter, bbox, color). Replace any prior rows for that
   `(job_id, page)`.
2. **List:** `GET /stage2/{page}/zones` → active zones `[{id, code, hex,
   area_sqft, bbox}]`.
3. **Delete by id:** `DELETE /zones/{zone_id}` → set `status='deleted'`, then
   re-render the overlay + recompute per-code totals from the remaining **active**
   zones, and refresh `stage2_results.data` (so the existing Materials list and
   pricing keep working).
4. **Restore:** `POST /zones/{zone_id}/restore` → set `status='active'`, re-render.
5. **Click endpoints** keep working: a click resolves to a `zone_id`, which then
   routes through the same delete path.

## Rendering from zones

`zones.render_from_zones(pdf, page, active_zones, out_png)` rasterizes each active
zone polygon (PDF points × dpi/72) onto the base page in its code color, blends,
and returns the recomputed `groups`. No dependency on the `.npz` for edits.

## API summary

| Method | Path | Action |
|--------|------|--------|
| GET | `/api/jobs/{job}/stage2/{page}/zones` | list active (or `?include_deleted=1`) |
| GET | `/api/jobs/{job}/zones/{id}` | one zone (with geometry) |
| DELETE | `/api/jobs/{job}/zones/{id}` | soft-delete + re-render |
| POST | `/api/jobs/{job}/zones/{id}/restore` | restore + re-render |

## Frontend

A **Zones** panel beside the overlay: lists active zones (id short, code swatch,
area). Hovering/selecting highlights its bbox on the overlay; a per-row 🗑 deletes
by id; a "Deleted" section offers restore. Reuses the existing overlay image +
bbox highlight components.

## Testing

- `store`: insert/replace, list active vs deleted, get, set status.
- `zones`: extract from a synthetic label → N rows with polygons/areas; render
  from zones reproduces per-code totals; delete drops one region's area.
- API: delete-by-id removes the zone from the active list and reduces its code's
  total; restore brings it back.

## Out of scope (now)

- Populating zones for the pool/color detection paths (only the engine path
  persists a label today); those can be wired the same way later.
- Multi-level undo history beyond the soft-delete flag.
