# Pool Scope Panel + Pool/Spa Zone Annotation

**Date:** 2026-06-22  
**Status:** Approved  

---

## Problem

When a swimming pool job is loaded, the UI shows no scope detail and the pool/spa shapes on the drawing are unaddressable — no colored zones, no geometry, no editing. Estimators can't see the spec while reviewing the takeoff, and the pool surface areas aren't in the zone editor like landscape areas are.

---

## Goals

1. Parse the swimming pool scope-of-work section from the uploaded estimate PDF into structured line items and display them as a side panel in Stage 2.
2. Detect and annotate the pool, spa, and any other SF-targeted water surfaces on the pool drawing as editable colored zones — same interaction model as landscape areas.

---

## Architecture

```
Estimate PDF
  ├─ estimate_parse.py (existing)  →  pool/spa SF targets  {POOL: 1109, SPA: 161, ...}
  └─ pool_scope.py (NEW)           →  structured scope items stored in config["pool_scope"]

Pool Drawing PDF (Stage 2)
  └─ pool_mode.py (MODIFIED)
       ├─ existing: match connected components by area → calibrate scale
       └─ NEW: extract contour polygons → replace_zones() → overlay from zones

API
  ├─ GET /api/jobs/{job_id}/pool-scope          (NEW)
  └─ GET /api/jobs/{job_id}/stage2/{page}/zones (EXISTING — now populated for pool pages)

Frontend
  └─ PoolScopePanel.jsx (NEW) — shown in Stage 2 when method == "pool"
```

`pool_scope.parse_pool_scope()` runs inside the existing `run_stage1_config` Celery task (parallel to the Gemini landscape config), so the scope is ready before the user reaches Stage 2.

---

## Feature 1: Pool Scope Parser

### Entry Point

**`backend/pool_scope.py`** — new file.

```python
def parse_pool_scope(pdf_path: Path, section_name: str = "SWIMMING POOL") -> dict | None
```

Returns `None` if the section is not found.

### Phase 1 — Deterministic Regex

Operates on the raw text extracted from the estimate PDF (`page.get_text()` via PyMuPDF).

| Target | Pattern |
|---|---|
| Section boundary | Line matching `^{section_name}` through next all-caps section header or EOF |
| Area SF | `\(?([\d,]+)\s*SF\)?` near the section header line |
| Total price | `{section_name}\s+Total:?\s*\$?([\d,]+\.\d{{2}})` |
| Numbered items | `^(\d{{1,2}})\.\s+(.+?)` (multi-line, up to next numbered item) |
| Sub-items | `^([a-h])\s*[–\-]\s*(.+)` within a numbered item |
| Sub-item quantity | `^(\d+)\s*[–\-]\s*` prefix on a sub-item line |

### Phase 2 — Gemini Classification

After regex extracts all items, send a compact JSON payload to Gemini asking only for `type` per item — no re-extraction. Prompt asks Gemini to classify each numbered item as one of:

`"labor" | "material" | "equipment" | "warranty" | "testing"`

Single API call, temperature=0, small payload (text only, no images).

**Fallback:** If Gemini fails or times out, all items get `type: null`. The panel renders without classification badges; no error surfaced to the user.

### Output Schema

Stored in DB `config` column under key `"pool_scope"`:

```json
{
  "scope_type": "SWIMMING POOL",
  "area_sf": 1109,
  "total_price": 549863.52,
  "items": [
    {
      "number": 1,
      "text": "Shop drawings, structural engineering, submittals...",
      "type": "labor",
      "sub_items": []
    },
    {
      "number": 5,
      "text": "Provide and install equipment for the pool...",
      "type": "equipment",
      "sub_items": [
        { "label": "a", "qty": 1, "description": "Pentair Intelliflo 5HP VS + SVRS pump", "unit": "ea" },
        { "label": "b", "qty": 1, "description": "Pentair Clean and Clear Cartridge filter", "unit": "ea" },
        { "label": "d", "qty": 12, "description": "Globrite/Microbrite Lights", "unit": "ea" }
      ]
    }
  ]
}
```

### Wiring (`tasks.py`)

In `run_stage1_config`, after the existing Gemini landscape config call:

```python
if estimate_pdf_path.exists():
    scope = pool_scope.parse_pool_scope(estimate_pdf_path)
    if scope:
        cfg = store.read_config(job_id) or {}
        cfg["pool_scope"] = scope
        store.write_config(job_id, cfg)
```

### New API Endpoint (`main.py`)

```
GET /api/jobs/{job_id}/pool-scope
→ 200: config["pool_scope"] dict
→ 404: if not yet parsed or section not found
```

---

## Feature 2: Pool/Spa Zone Annotation

### What Changes in `pool_mode.py`

After the existing greedy match assigns a connected component to each target surface:

1. **Extract contour polygon** from the binary component mask:
   ```python
   contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
   contour = max(contours, key=cv2.contourArea)
   simplified = cv2.approxPolyDP(contour, epsilon=2.0, closed=True)
   ```

2. **Convert pixels → PDF points:**
   ```python
   pt_scale = 72.0 / render_dpi
   geometry = [[[int(p[0][0] * pt_scale), int(p[0][1] * pt_scale)] for p in simplified]]
   ```

3. **Build zone dict** (same schema as landscape zones):
   ```json
   {
     "id": "<uuid hex>",
     "code": "POOL",
     "hex": "#e91e63",
     "area_sqft": 1109.2,
     "perimeter_lf": 132.4,
     "geometry": [[[x, y], ...]],
     "bbox": [x0_norm, y0_norm, x1_norm, y1_norm],
     "source": "pool"
   }
   ```

4. **Store:** call `store.replace_zones(job_id, page, zones)` instead of `[]`.

5. **Render overlay** via `zones.render_from_zones()` — drop the existing hand-drawn overlay code in `pool_mode.py` so pool and landscape overlays use the exact same rendering pipeline.

### Surface Color Map (existing, unchanged)

| Surface | Hex |
|---|---|
| POOL | `#e91e63` (magenta) |
| SPA | `#009688` (teal) |
| TANNING LEDGE | `#ffc107` (amber) |
| STONE STEPPERS | `#795548` (brown) |

### What Surfaces Get Zones

Exactly the surfaces returned by `estimate_parse.parse_pool_targets()` — whatever has an SF value in the estimate PDF. No hardcoded list.

---

## Feature 3: PoolScopePanel (Frontend)

### When It Shows

In `App.jsx` Stage 2 view: when `stage2Result.method === "pool"`, render `<PoolScopePanel jobId={jobId} />` as a collapsible right panel alongside the zone editor.

### Layout

```
┌─────────────────────────────────┐
│ 🏊 SWIMMING POOL  1,109 SF      │
│                    $549,863.52  │
├─────────────────────────────────┤
│ [labor]    1. Shop drawings...  │
│ [material] 2. Provide gunite... │
│ [equip]    5. Equipment         │
│   ├ 1× Pentair Intelliflo 5HP   │
│   ├ 1× Cartridge filter         │
│   └ 12× Globrite Lights         │
│ [warranty] 10. One-year warr... │
└─────────────────────────────────┘
```

- Type badges: color-coded chips (`labor`=blue, `material`=orange, `equipment`=purple, `warranty`=gray, `testing`=green)
- Sub-items indented under parent item, quantity bolded
- Panel is collapsible; default open
- Fetches `GET /api/jobs/{job_id}/pool-scope` once on mount; shows skeleton loader while pending; silently hides if 404

---

## Files Changed

| File | Change |
|---|---|
| `backend/pool_scope.py` | NEW — hybrid scope parser |
| `backend/pool_mode.py` | MODIFY — add contour extraction + zone storage + use shared overlay renderer |
| `backend/tasks.py` | MODIFY — call `parse_pool_scope` in `run_stage1_config` |
| `backend/main.py` | MODIFY — add `GET /api/jobs/{job_id}/pool-scope` endpoint |
| `frontend/src/PoolScopePanel.jsx` | NEW — scope panel component |
| `frontend/src/App.jsx` | MODIFY — render `PoolScopePanel` when `method === "pool"` |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Estimate PDF has no pool section | `parse_pool_scope` returns `None`; no scope stored; panel hidden (404) |
| Gemini classification fails | Items stored with `type: null`; panel renders without badges |
| Pool region not found for a target | That surface skipped; only detected surfaces get zones |
| `findContours` returns empty | Fall back to bounding-box polygon for that zone |

---

## Out of Scope

- Editing pool scope items in the UI (read-only panel)
- Multi-pool jobs (one pool section per estimate assumed)
- Spa scope parsing as a separate section (spa is a sub-surface of the pool section)
