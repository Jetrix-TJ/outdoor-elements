# Pool Scope Panel + Pool/Spa Zone Annotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse the swimming pool scope-of-work from the estimate PDF into structured line items (displayed as a side panel in Stage 2), and convert pool/spa detected regions into editable zone records identical to landscape zones.

**Architecture:** A new `backend/pool_scope.py` module (regex + Gemini hybrid parser) is called from `run_stage1_config` and stored under `config["pool_scope"]`; a new `GET /api/jobs/{id}/pool-scope` endpoint surfaces it. `pool_mode.detect_pool` is extended to extract contour polygons from each matched region and call `replace_zones`, so the pool path produces addressable zone rows; the overlay is then rendered via the shared `zones.render_from_zones` instead of hand-drawn pixel fills. A new `PoolScopePanel.jsx` React component renders the scope items alongside the zone editor whenever `stage2.method === "pool"`.

**Tech Stack:** Python 3.14, FastAPI, PyMuPDF (fitz), OpenCV (cv2), NumPy, google-generativeai, React 18, Vite

## Global Constraints

- All backend tests use the SQLite conftest (never touch real Postgres): `DATABASE_URL=sqlite:///...` set in `backend/tests/conftest.py`
- Run backend tests from the project root: `.venv/bin/pytest backend/tests/<file> -v`
- Gemini model name: `"gemini-3.5-flash"` (same as `gemini_config.py`)
- Zone geometry is in PDF points (1 pt = 1/72 inch); convert from pixels via `pt_scale = 72.0 / dpi`
- Zone `hex` field uses lowercase 6-digit hex e.g. `"#e91e63"`
- `store.replace_zones(job_id, page, zones)` returns a list of assigned zone IDs
- Pool surface colors live in `pool_mode._SURFACE_COLOR` (RGB tuples); convert to hex for zone records
- Frontend runs from `frontend/` dir: `npm run dev` (Vite on port 5173)

---

### Task 1: `pool_scope.py` — regex parser + unit tests

**Files:**
- Create: `backend/pool_scope.py`
- Create: `backend/tests/test_pool_scope.py`

**Interfaces:**
- Produces: `parse_pool_scope(pdf_path, section_name, api_key) -> dict | None` (used by Task 3)
- Produces: `_find_section(text, section_name) -> str | None` (internal, tested directly)
- Produces: `_parse_items(section_text) -> list[dict]` (internal, tested directly)

- [ ] **Step 1: Write failing tests for `_find_section` and `_parse_items`**

```python
# backend/tests/test_pool_scope.py
"""Unit tests for pool_scope regex parser. No PDF needed — pass text directly."""

SAMPLE_SECTION = """\
SWIMMING POOL (1,109 SF)
1. Shop drawings, structural engineering, submittals, and Operation &
Maintenance manuals. Two (2) design and coordination meetings.
2. Provide and install Gunite 8" thick and reinforcement #4 - 10" o.c.e.w.
5. Provide and install equipment for the pool in equipment room located near body of
water:
a. 1 – Pentair Intelliflo 5HP VS + SVRS pump for body of water.
b. 1 – Pentair Clean and Clear Cartridge filter for body of water.
d. 12 – Globrite/Microbrite Lights per OE selection.
10. One-year warranty on installation and materials.
Swimming Pool Total: $549,863.52
"""

FULL_DOC = """\
LANDSCAPE
Some landscape content.
Landscape Total: $100,000.00

SWIMMING POOL (1,109 SF)
1. Shop drawings and coordination.
Swimming Pool Total: $549,863.52

HARDSCAPE
Some hardscape content.
"""


def test_find_section_returns_pool_block():
    from backend.pool_scope import _find_section
    result = _find_section(FULL_DOC, "SWIMMING POOL")
    assert result is not None
    assert "Shop drawings" in result
    assert "HARDSCAPE" not in result


def test_find_section_returns_none_when_missing():
    from backend.pool_scope import _find_section
    assert _find_section("no pool here", "SWIMMING POOL") is None


def test_parse_sf_from_header():
    from backend.pool_scope import _parse_sf
    assert _parse_sf("SWIMMING POOL (1,109 SF)") == 1109
    assert _parse_sf("SWIMMING POOL 1109 SF") == 1109
    assert _parse_sf("no sf here") is None


def test_parse_total():
    from backend.pool_scope import _parse_total
    assert _parse_total(SAMPLE_SECTION, "SWIMMING POOL") == 549863.52
    assert _parse_total("no total", "SWIMMING POOL") is None


def test_parse_items_numbered():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    numbers = [it["number"] for it in items]
    assert 1 in numbers
    assert 2 in numbers
    assert 10 in numbers


def test_parse_items_sub_items():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    item5 = next(it for it in items if it["number"] == 5)
    assert len(item5["sub_items"]) >= 3
    sub_a = next(s for s in item5["sub_items"] if s["label"] == "a")
    assert sub_a["qty"] == 1
    assert "Pentair" in sub_a["description"]
    sub_d = next(s for s in item5["sub_items"] if s["label"] == "d")
    assert sub_d["qty"] == 12


def test_parse_items_type_is_none_without_gemini():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    assert all(it["type"] is None for it in items)


def test_parse_pool_scope_returns_none_for_missing_section():
    import tempfile, os
    from pathlib import Path
    import fitz
    from backend.pool_scope import parse_pool_scope
    # create a tiny PDF with no pool section
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), "LANDSCAPE\nSome content.\n")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    try:
        assert parse_pool_scope(tmp) is None
    finally:
        os.unlink(tmp)


def test_parse_pool_scope_full():
    import tempfile, os
    import fitz
    from backend.pool_scope import parse_pool_scope
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), SAMPLE_SECTION)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    try:
        result = parse_pool_scope(tmp)  # no api_key → no Gemini
        assert result is not None
        assert result["scope_type"] == "SWIMMING POOL"
        assert result["area_sf"] == 1109
        assert result["total_price"] == 549863.52
        assert len(result["items"]) >= 3
    finally:
        os.unlink(tmp)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest backend/tests/test_pool_scope.py -v
```
Expected: `ImportError: cannot import name '_find_section' from 'backend.pool_scope'` (module doesn't exist yet)

- [ ] **Step 3: Create `backend/pool_scope.py`**

```python
"""Parse the OE estimate PDF swimming pool scope of work into structured items.

Phase 1 (deterministic): regex extracts numbered items, sub-items, quantities,
total price, and area SF from the raw PDF text.
Phase 2 (Gemini): classify each item type. Graceful fallback: type stays None.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz


def _extract_text(pdf_path: str | Path) -> str:
    doc = fitz.open(str(pdf_path))
    text = "\n".join(pg.get_text() for pg in doc)
    doc.close()
    return text


def _find_section(text: str, section_name: str) -> str | None:
    """Return the text block starting at section_name through the next all-caps header."""
    escaped = re.escape(section_name.upper())
    pattern = (
        rf"(?m)^(?:{escaped}[^\n]*\n)"  # header line
        rf"(.*?)"                         # body
        rf"(?=\n[A-Z][A-Z &/]{{3,}}[^\n]*\n|\Z)"  # next header or EOF
    )
    m = re.search(pattern, text.upper(), re.DOTALL)
    if not m:
        return None
    # return original-case slice that aligns with the match position
    start = m.start()
    end = m.end()
    return text[start:end]


def _parse_sf(header_text: str) -> int | None:
    m = re.search(r"\(?([\d,]+)\s*SF\)?", header_text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_total(section_text: str, section_name: str) -> float | None:
    m = re.search(
        rf"(?i){re.escape(section_name)}\s+Total:?\s*\$?([\d,]+\.\d{{2}})",
        section_text,
    )
    return float(m.group(1).replace(",", "")) if m else None


def _parse_items(section_text: str) -> list[dict]:
    """Split section into numbered items, each with optional sub-items (a–h)."""
    # Split on lines that start a new numbered item
    item_blocks = re.split(r"(?m)^(?=\d{1,2}\.\s)", section_text)
    items = []
    for block in item_blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^(\d{1,2})\.\s+(.+)", block, re.DOTALL)
        if not m:
            continue
        number = int(m.group(1))
        body = m.group(2).strip()

        # sub-items: lines starting with "a." through "h."
        sub_items: list[dict] = []
        for sm in re.finditer(
            r"(?m)^([a-h])\.\s+(.+?)(?=^[a-h]\.\s|\Z)", body, re.DOTALL
        ):
            label = sm.group(1)
            desc_raw = sm.group(2).strip().replace("\n", " ")
            # optional leading quantity: "12 – ..." or "1 – ..."
            qty_m = re.match(r"^(\d+)\s*[–\-]\s*(.+)", desc_raw)
            if qty_m:
                qty = int(qty_m.group(1))
                desc = qty_m.group(2).strip()
            else:
                qty = 1
                desc = desc_raw
            sub_items.append({"label": label, "qty": qty,
                               "description": desc, "unit": "ea"})

        # item text: everything up to the first sub-item (or full body)
        split_m = re.search(r"(?m)^[a-h]\.\s", body)
        item_text = body[:split_m.start()].strip() if split_m else body
        item_text = " ".join(item_text.split())  # collapse whitespace

        items.append({
            "number": number,
            "text": item_text,
            "type": None,
            "sub_items": sub_items,
        })
    return items


def _classify_with_gemini(items: list[dict], api_key: str) -> list[dict]:
    """Classify each item type via Gemini. Falls back silently on any error."""
    import google.generativeai as genai  # imported lazily — only needed with api_key
    payload = [{"number": it["number"], "text": it["text"][:200]} for it in items]
    prompt = (
        "Classify each numbered scope-of-work item for a swimming pool construction project.\n"
        "Valid types: labor, material, equipment, warranty, testing.\n\n"
        "Items:\n" + json.dumps(payload, indent=2) + "\n\n"
        "Respond with a JSON array: [{\"number\": 1, \"type\": \"labor\"}, ...]\n"
        "Output ONLY the raw JSON array, no markdown, no explanation."
    )
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-3.5-flash",
            generation_config={"temperature": 0, "max_output_tokens": 1024},
        )
        raw = model.generate_content(prompt).text
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
        classifications = {
            entry["number"]: entry["type"] for entry in json.loads(raw.strip())
        }
        for item in items:
            item["type"] = classifications.get(item["number"])
    except Exception:  # noqa: BLE001
        pass  # leave type=None — panel renders without badges
    return items


def parse_pool_scope(
    pdf_path: str | Path,
    section_name: str = "SWIMMING POOL",
    api_key: str | None = None,
) -> dict | None:
    """Parse the named scope section from an estimate PDF.

    Returns structured dict or None if section not found.
    Pass api_key to enable Gemini type classification; omit for text-only parse.
    """
    text = _extract_text(pdf_path)
    section = _find_section(text, section_name)
    if not section:
        return None

    header_line = section.split("\n")[0]
    area_sf = _parse_sf(header_line)
    total_price = _parse_total(section, section_name)
    items = _parse_items(section)

    if api_key and items:
        items = _classify_with_gemini(items, api_key)

    return {
        "scope_type": section_name,
        "area_sf": area_sf,
        "total_price": total_price,
        "items": items,
    }
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
.venv/bin/pytest backend/tests/test_pool_scope.py -v
```
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pool_scope.py backend/tests/test_pool_scope.py
git commit -m "feat: pool_scope.py — hybrid regex+Gemini parser for pool scope section"
```

---

### Task 2: Wire scope parser into config task + API endpoint

**Files:**
- Modify: `backend/tasks.py` — add `parse_pool_scope` call in `run_stage1_config`
- Modify: `backend/main.py` — add `GET /api/jobs/{job_id}/pool-scope`

**Interfaces:**
- Consumes: `pool_scope.parse_pool_scope(pdf_path, api_key=...)` from Task 1
- Consumes: `store.estimate_path(job_id)` → `Path` (already exists in store.py:66)
- Consumes: `store.read_config(job_id)`, `store.write_config(job_id, cfg)` (already exist)
- Produces: `GET /api/jobs/{job_id}/pool-scope` → `dict` (consumed by frontend Task 4)

- [ ] **Step 1: Write a failing integration test**

```python
# backend/tests/test_pool_scope_wiring.py
"""Test that run_stage1_config writes pool_scope into config."""
import os, tempfile
import fitz
import pytest
from backend import db, store
from backend.tasks import run_stage1_config


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()


SCOPE_TEXT = """\
SWIMMING POOL (1,109 SF)
1. Shop drawings and coordination.
Swimming Pool Total: $549,863.52
"""


def _make_pdf(text: str) -> str:
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), text)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    return tmp


def test_run_stage1_config_writes_pool_scope(tmp_path, monkeypatch):
    _fresh_db()
    job_id = "testjob01"
    # set up job dir and fake PDFs
    jd = store.job_dir(job_id)
    jd.mkdir(parents=True, exist_ok=True)

    # main PDF (empty — config build will fail gracefully)
    fitz.open().save(str(store.pdf_path(job_id)))

    # estimate PDF with pool scope
    est_pdf = _make_pdf(SCOPE_TEXT)
    import shutil
    shutil.copy(est_pdf, str(store.estimate_path(job_id)))
    os.unlink(est_pdf)

    store.write_status(job_id, {"job_id": job_id, "status": "queued"})

    # stub out Gemini so test doesn't need network
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr("backend.pool_scope._classify_with_gemini",
                        lambda items, api_key: items)
    monkeypatch.setattr("backend.gemini_config.build_config",
                        lambda *a, **kw: {"sheets": {}, "materials": {}, "source": "fallback"})

    run_stage1_config(job_id, "test.pdf")

    cfg = store.read_config(job_id)
    assert cfg is not None
    assert "pool_scope" in cfg
    scope = cfg["pool_scope"]
    assert scope["scope_type"] == "SWIMMING POOL"
    assert scope["area_sf"] == 1109
    assert scope["total_price"] == 549863.52
    assert len(scope["items"]) >= 1
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest backend/tests/test_pool_scope_wiring.py -v
```
Expected: FAIL — `pool_scope` key not in config (wiring not added yet)

- [ ] **Step 3: Add `pool_scope` import and wiring to `tasks.py`**

In `backend/tasks.py`, add `pool_scope` to the existing import line at line 13:

```python
from . import (estimate_parse, gemini_config, pool_mode, pool_scope, qto_engine, selection,
               stage2, store, zones)
```

In `run_stage1_config`, after `store.write_config(job_id, cfg)` (line ~90), add:

```python
    # Parse pool scope from estimate PDF (if present) and merge into config.
    ep = store.estimate_path(job_id)
    if ep.exists():
        scope = pool_scope.parse_pool_scope(
            str(ep),
            api_key=os.environ.get("GEMINI_API_KEY"),
        )
        if scope:
            cfg = store.read_config(job_id) or {}
            cfg["pool_scope"] = scope
            store.write_config(job_id, cfg)
```

- [ ] **Step 4: Add `GET /api/jobs/{job_id}/pool-scope` to `main.py`**

After the existing `get_config` endpoint (around line 113), add:

```python
@app.get("/api/jobs/{job_id}/pool-scope")
def get_pool_scope(job_id: str) -> dict:
    cfg = store.read_config(job_id)
    if cfg is None or "pool_scope" not in cfg:
        raise HTTPException(status_code=404, detail="Pool scope not available.")
    return cfg["pool_scope"]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest backend/tests/test_pool_scope_wiring.py -v
```
Expected: PASS

- [ ] **Step 6: Smoke-test the endpoint with curl (restart backend first)**

```bash
pkill -f "uvicorn backend.main:app" && sleep 1
.venv/bin/uvicorn backend.main:app --reload --port 8000 > /tmp/oe-backend.log 2>&1 &
sleep 6 && curl -s http://localhost:8000/api/jobs/NONEXISTENT/pool-scope
```
Expected: `{"detail":"Pool scope not available."}`

- [ ] **Step 7: Commit**

```bash
git add backend/tasks.py backend/main.py backend/tests/test_pool_scope_wiring.py
git commit -m "feat: wire pool scope parser into run_stage1_config, add pool-scope API endpoint"
```

---

### Task 3: `pool_mode.py` — contour extraction + zone storage

**Files:**
- Modify: `backend/pool_mode.py` — extract polygon geometry, return zones, use shared renderer
- Modify: `backend/tasks.py` — store zones from detect_pool result, build groups from zones
- Create: `backend/tests/test_pool_mode_zones.py`

**Interfaces:**
- Consumes: `zones.render_from_zones(pdf_path, page_idx, zone_list, out_png, dpi)` → `{"groups": [...], "overlay": "..."}` (zones.py:91)
- Consumes: `zones.groups_from_zones(zone_list)` → `list[dict]` (zones.py:77)
- Produces: `detect_pool(...)` now returns `"zones": list[dict]` in addition to `"surfaces"`, `"overlay"`, `"scale_in_per_ft"`
- Produces: `tasks.py` pool path calls `store.replace_zones(job_id, page, res["zones"])`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_pool_mode_zones.py
"""Test that detect_pool returns zone geometry and uses shared renderer."""
import tempfile, os, shutil
import numpy as np
import fitz
import pytest


def _make_pool_pdf(out_path: str, pool_rect, spa_rect, page_size=(612, 792)):
    """Create a minimal PDF with two thick-bordered rectangles (pool + spa)."""
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    # thick black border for pool
    page.draw_rect(fitz.Rect(*pool_rect), color=(0, 0, 0), width=3, fill=None)
    # thick black border for spa
    page.draw_rect(fitz.Rect(*spa_rect), color=(0, 0, 0), width=3, fill=None)
    doc.save(out_path)
    doc.close()


def test_detect_pool_returns_zones(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = str(tmp_path / "overlay.png")
    # pool ~200x150 pts at 150dpi; spa ~80x60 pts
    _make_pool_pdf(pdf,
                   pool_rect=(50, 50, 250, 200),
                   spa_rect=(300, 50, 380, 110))
    targets = {"POOL": 1.0, "SPA": 0.1}  # rough SF — just need match
    result = pool_mode.detect_pool(pdf, 0, targets, out_png, dpi=72)
    assert "zones" in result
    zones = result["zones"]
    assert len(zones) >= 1
    for z in zones:
        assert "id" in z
        assert "code" in z
        assert "hex" in z and z["hex"].startswith("#")
        assert "geometry" in z and len(z["geometry"]) > 0
        assert "area_sqft" in z
        assert "source" in z and z["source"] == "pool"


def test_detect_pool_overlay_exists(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = tmp_path / "overlay_p0.png"
    _make_pool_pdf(pdf, pool_rect=(50, 50, 250, 200), spa_rect=(300, 50, 380, 110))
    pool_mode.detect_pool(pdf, 0, {"POOL": 1.0}, str(out_png), dpi=72)
    assert out_png.exists()


def test_detect_pool_zone_geometry_in_pdf_points(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = str(tmp_path / "overlay.png")
    _make_pool_pdf(pdf, pool_rect=(50, 50, 250, 200), spa_rect=(300, 50, 380, 110))
    result = pool_mode.detect_pool(pdf, 0, {"POOL": 1.0}, out_png, dpi=72)
    for z in result["zones"]:
        for poly in z["geometry"]:
            for pt in poly:
                # points should be in PDF-point range (page is 612x792 pts)
                assert 0 <= pt[0] <= 612, f"x out of range: {pt[0]}"
                assert 0 <= pt[1] <= 792, f"y out of range: {pt[1]}"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest backend/tests/test_pool_mode_zones.py -v
```
Expected: FAIL — `"zones" not in result` (detect_pool doesn't return zones yet)

- [ ] **Step 3: Modify `backend/pool_mode.py`**

Add `uuid` and `zones` imports at the top:

```python
import uuid
from . import qto_engine, zones as zones_mod
```

Replace the entire section after `px_per_sf = (scale_in_per_ft * dpi) ** 2` through the end of the function with:

```python
    pt_scale = 72.0 / dpi
    page_h, page_w = binary.shape

    zone_list: list[dict] = []
    surfaces = []
    for name, i, a in matched:
        mask = (labels == i).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perim_px = sum(cv2.arcLength(c, True) for c in cnts)

        # extract polygon geometry in PDF points
        polys = []
        for cnt in cnts:
            simplified = cv2.approxPolyDP(cnt, 2.0, True)
            if len(simplified) >= 3:
                polys.append([
                    [float(p[0][0]) * pt_scale, float(p[0][1]) * pt_scale]
                    for p in simplified
                ])

        # normalized bounding box
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y_top = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        bbox = [x / page_w, y_top / page_h,
                (x + cw) / page_w, (y_top + ch) / page_h]

        rgb = _SURFACE_COLOR.get(name.upper(), _DEFAULT_COLOR)
        hex_color = "#%02x%02x%02x" % rgb
        area_sf = round(a / px_per_sf, 1)
        perim_lf = round(perim_px * (1.0 / dpi) / scale_in_per_ft, 1)

        surfaces.append({
            "name": name,
            "area_sf": area_sf,
            "perimeter_lf": perim_lf,
            "target_sf": float(targets[name]),
        })

        if polys:
            zone_list.append({
                "id": uuid.uuid4().hex[:16],
                "code": name,
                "hex": hex_color,
                "area_sqft": area_sf,
                "perimeter_lf": perim_lf,
                "geometry": polys,
                "bbox": bbox,
                "source": "pool",
            })

    # render overlay via shared zones pipeline (consistent with landscape)
    zones_mod.render_from_zones(str(pdf_path), page_idx, zone_list, Path(out_png), dpi=dpi)

    return {
        "surfaces": surfaces,
        "overlay": Path(out_png).name,
        "scale_in_per_ft": scale_in_per_ft,
        "zones": zone_list,
    }
```

Also add `import uuid` and `from . import qto_engine, zones as zones_mod` at the top of the file (replace the existing `from . import qto_engine` line):

```python
import uuid
from . import qto_engine
from . import zones as zones_mod
```

- [ ] **Step 4: Update `tasks.py` pool path to store zones and use zone-derived groups**

Find the block in `run_stage2` / `run_stage2` that currently reads:
```python
            res = pool_mode.detect_pool(pdf, page, pool_targets, out, dpi=120)
            store.replace_zones(job_id, page, [])  # pool path not zone-addressable yet
            groups = [
                {"label": s["name"], "sqft": s["area_sf"], "regions": 1,
                 "perimeter_lf": s.get("perimeter_lf"),
                 "hex": "#%02x%02x%02x" % pool_mode._SURFACE_COLOR.get(
                     s["name"].upper(), pool_mode._DEFAULT_COLOR)}
                for s in res["surfaces"]
            ]
```

Replace with:

```python
            res = pool_mode.detect_pool(pdf, page, pool_targets, out, dpi=120)
            store.replace_zones(job_id, page, res.get("zones", []))
            groups = zones.groups_from_zones(res.get("zones", []))
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest backend/tests/test_pool_mode_zones.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/pool_mode.py backend/tasks.py backend/tests/test_pool_mode_zones.py
git commit -m "feat: pool_mode extracts zone geometry + stores via replace_zones, overlay from shared renderer"
```

---

### Task 4: `PoolScopePanel.jsx` + `api.js` + `App.jsx` integration

**Files:**
- Create: `frontend/src/PoolScopePanel.jsx`
- Modify: `frontend/src/api.js` — add `getPoolScope`
- Modify: `frontend/src/App.jsx` — import and render `PoolScopePanel` when `method === "pool"`

**Interfaces:**
- Consumes: `GET /api/jobs/{job_id}/pool-scope` → `{scope_type, area_sf, total_price, items[]}` (Task 2)
- Consumes: `s2.method` from Stage 2 result (already in `s2` state: `s2?.method === "pool"`)

- [ ] **Step 1: Add `getPoolScope` to `frontend/src/api.js`**

Add after the last existing export:

```js
export async function getPoolScope(jobId) {
  const res = await fetch(`${API}/jobs/${jobId}/pool-scope`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to load pool scope");
  return res.json();
}
```

- [ ] **Step 2: Create `frontend/src/PoolScopePanel.jsx`**

```jsx
import { useEffect, useState } from "react";
import { getPoolScope } from "./api.js";

const TYPE_BADGE = {
  labor:     { label: "labor",     color: "#1976d2" },
  material:  { label: "material",  color: "#e65100" },
  equipment: { label: "equipment", color: "#6a1b9a" },
  warranty:  { label: "warranty",  color: "#546e7a" },
  testing:   { label: "testing",   color: "#2e7d32" },
};

function Badge({ type }) {
  if (!type) return null;
  const b = TYPE_BADGE[type] || { label: type, color: "#888" };
  return (
    <span style={{
      display: "inline-block", padding: "1px 7px", borderRadius: 10,
      fontSize: 10, fontWeight: 600, color: "#fff", background: b.color,
      marginRight: 6, verticalAlign: "middle", textTransform: "uppercase",
      letterSpacing: "0.04em",
    }}>
      {b.label}
    </span>
  );
}

function fmt(n) {
  return n == null ? "—" : Number(n).toLocaleString();
}

export default function PoolScopePanel({ jobId }) {
  const [scope, setScope] = useState(undefined); // undefined=loading, null=not found
  const [open, setOpen] = useState(true);

  useEffect(() => {
    getPoolScope(jobId)
      .then(setScope)
      .catch(() => setScope(null));
  }, [jobId]);

  if (scope === undefined) {
    return (
      <div className="pool-scope-panel loading">
        <div className="skeleton sk-line w80" />
        <div className="skeleton sk-line" />
        <div className="skeleton sk-line w60" />
      </div>
    );
  }
  if (!scope) return null;

  return (
    <div className="pool-scope-panel">
      <button
        className="pool-scope-header"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        <span className="pool-scope-title">
          {scope.scope_type}
        </span>
        <span className="pool-scope-meta">
          {fmt(scope.area_sf)} SF
          {scope.total_price != null && (
            <> · ${fmt(Math.round(scope.total_price))}</>
          )}
        </span>
        <span className="pool-scope-chevron">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <ol className="pool-scope-items">
          {(scope.items || []).map(item => (
            <li key={item.number} className="pool-scope-item">
              <div className="pool-scope-item-row">
                <Badge type={item.type} />
                <span className="pool-scope-item-text">{item.text}</span>
              </div>
              {item.sub_items?.length > 0 && (
                <ul className="pool-scope-subitems">
                  {item.sub_items.map(s => (
                    <li key={s.label} className="pool-scope-subitem">
                      <span className="sub-qty">{s.qty}×</span>
                      {" "}{s.description}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add styles to `frontend/src/index.css` (or App.css)**

Find the existing CSS file used by the app:

```bash
ls /Users/aravindkrishnam/repos/outdoor-elements/frontend/src/*.css
```

Add at the end of whichever CSS file exists:

```css
/* ── Pool Scope Panel ─────────────────────────────────────────────────────── */
.pool-scope-panel {
  border: 1px solid #dde;
  border-radius: 8px;
  background: #fafbff;
  margin-top: 16px;
  overflow: hidden;
}
.pool-scope-panel.loading {
  padding: 14px 16px;
}
.pool-scope-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 12px 16px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}
.pool-scope-title {
  font-weight: 700;
  color: #1a1a2e;
  flex: 1;
}
.pool-scope-meta {
  color: #555;
  font-size: 12px;
}
.pool-scope-chevron {
  font-size: 10px;
  color: #888;
  margin-left: 4px;
}
.pool-scope-items {
  list-style: none;
  margin: 0;
  padding: 0 16px 12px;
  counter-reset: item;
}
.pool-scope-item {
  padding: 6px 0;
  border-top: 1px solid #eef;
  font-size: 12px;
  line-height: 1.5;
}
.pool-scope-item-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.pool-scope-item-text {
  color: #333;
}
.pool-scope-subitems {
  list-style: none;
  margin: 4px 0 0 16px;
  padding: 0;
}
.pool-scope-subitem {
  font-size: 11px;
  color: #444;
  padding: 2px 0;
}
.sub-qty {
  font-weight: 700;
  color: #1976d2;
  min-width: 24px;
  display: inline-block;
}
```

- [ ] **Step 4: Import `PoolScopePanel` and `getPoolScope` in `App.jsx`**

Add to the existing import at the top of `frontend/src/App.jsx`:

```js
import PoolScopePanel from "./PoolScopePanel.jsx";
```

Add `getPoolScope` to the existing api.js import line (it's already there from Step 1 — just verify it's included).

- [ ] **Step 5: Render `PoolScopePanel` in the Stage 2 side panel**

In `App.jsx`, find the `s2?.status === "done"` block that renders the `<div className="s2side">`. Inside `s2side`, after the existing groups/zones list and before the closing `</div>`, add:

```jsx
{s2?.method === "pool" && (
  <PoolScopePanel jobId={job.job_id} />
)}
```

- [ ] **Step 6: Verify in browser**

```bash
# Ensure backend + celery + frontend are running (see earlier session commands)
# Upload a pool PDF and navigate to Stage 2
# Click a pool page — should see colored zones + scope panel below the materials list
```

Open http://localhost:5173, log in (passcode: 2811), upload the pool PDF, navigate to Stage 2, click a pool page. Verify:
- Colored zones appear on the drawing (magenta for POOL, teal for SPA)
- Scope panel appears below the materials panel
- Items are numbered, sub-items indented with quantities
- Panel collapses/expands on click

- [ ] **Step 7: Commit**

```bash
git add frontend/src/PoolScopePanel.jsx frontend/src/api.js frontend/src/App.jsx frontend/src/*.css
git commit -m "feat: PoolScopePanel — display pool scope line items alongside zone editor"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Parse scope from estimate PDF (hybrid regex+Gemini) | Task 1 |
| Numbered items, sub-items with quantities, total price, area SF | Task 1 |
| Gemini classifies item types; fallback if fails | Task 1 (`_classify_with_gemini`) |
| Run in `run_stage1_config`, stored under `config["pool_scope"]` | Task 2 |
| `GET /api/jobs/{id}/pool-scope` returns 404 if absent | Task 2 |
| Zone geometry extracted from matched components | Task 3 |
| Geometry in PDF points via `pt_scale = 72.0 / dpi` | Task 3 |
| Zones stored via `replace_zones` (not empty list) | Task 3 |
| Overlay rendered via `zones.render_from_zones` | Task 3 |
| Detect whatever surfaces have SF targets (not hardcoded list) | Task 3 — targets come from `_load_pool_targets` which calls `estimate_parse.parse_pool_targets` |
| `PoolScopePanel` shown when `method === "pool"` | Task 4 |
| Panel: header with scope type + SF + total, items with type badges, sub-items with qty | Task 4 |
| Collapsible panel, skeleton while loading, hidden on 404 | Task 4 |

**No placeholders found.**

**Type consistency:** `detect_pool` returns `"zones": list[dict]` — tasks.py accesses `res.get("zones", [])` ✓. `getPoolScope` returns `null` on 404 — `PoolScopePanel` checks `if (!scope) return null` ✓. Zone schema in pool_mode matches `replace_zones` expectation (code, hex, area_sqft, geometry, bbox, source) ✓.
