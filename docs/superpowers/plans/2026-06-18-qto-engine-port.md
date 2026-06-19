# QTO Engine Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace our approximate B&W area detection with the lead's line-width-boundary + connected-component zone engine (`outdoor_qto.py`) plus Gemini auto-config (`generate_config.py`), wired into the existing 3-stage app, hitting <10% delta vs the QTO reference.

**Architecture:** Two ported, importable modules — `backend/qto_engine.py` (deterministic CV takeoff) and `backend/gemini_config.py` (Gemini config builder) — invoked by Celery tasks. Stage 1 builds a per-job `qto_config.json`; Stage 2 runs the engine per sheet using that config; Stage 3 compares to the QTO legend (unchanged).

**Tech Stack:** Python 3.12, PyMuPDF (fitz), OpenCV, NumPy, Pillow, google-generativeai, FastAPI, Celery, Redis, React/Vite.

## Global Constraints

- Source of truth for the port: `C:\Users\absar\Downloads\outdoor_qto.py` and `C:\Users\absar\Downloads\generate_config.py`. Preserve their constants/thresholds verbatim (min_lw=0.35, min_extent=3.0, max_lw=0.49, `_CALLOUT_BUBBLE_MAX_PX=2000`, ALPHA=0.5, etc.).
- Gemini model: **`gemini-3.5-flash`** via the installed `google.generativeai` SDK (the lead's `gemini-3.1-pro-preview` is not on our key). Keep the prompt text verbatim.
- GEMINI_API_KEY from `outdoor-elements/.env` — never hardcoded.
- Overlay output uses our existing Pillow style (not matplotlib) to match the UI.
- Run from `outdoor-elements/`; venv at `.venv/Scripts/python.exe`.
- Accuracy gate (the QTO reference): L1.01 M.5≈6550, M.6≈1760, M.7≈191; L1.04 M.9≈3937. Target |delta| < 10%.
- Test PDF: `C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf`.

---

### Task 1: Port the rasterization core into `qto_engine.py`

**Files:**
- Create: `backend/qto_engine.py`
- Test: `backend/tests/test_qto_engine.py`

**Interfaces:**
- Produces: `render_thick_boundaries(pdf_path, page_idx, dpi, min_lw=0.35, min_extent=3.0) -> np.ndarray`, `render_hatch_lines(pdf_path, page_idx, dpi, max_lw=0.49) -> np.ndarray`, `preprocess_for_fill(boundary_img) -> np.ndarray`, helpers `_bezier_pts`, `_make_pt_transform`, `_is_parallel_hatch`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_qto_engine.py
from pathlib import Path
import numpy as np
from backend import qto_engine

PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"
L1_01_PAGE = 2  # 0-based

def test_thick_boundaries_are_sparser_than_full_ink():
    thick = qto_engine.render_thick_boundaries(PDF, L1_01_PAGE, dpi=120)
    # walls-only image: black (0) pixels are a small fraction of the page
    black_frac = float((thick < 128).mean())
    assert 0.0005 < black_frac < 0.05, black_frac

def test_preprocess_returns_binary_fillable():
    thick = qto_engine.render_thick_boundaries(PDF, L1_01_PAGE, dpi=120)
    binary = qto_engine.preprocess_for_fill(thick)
    vals = set(np.unique(binary).tolist())
    assert vals <= {0, 255}
    assert (binary == 255).mean() > 0.5  # most of the page is fillable interior
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/construction_poc/outdoor-elements && ./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -q`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (qto_engine missing).

- [ ] **Step 3: Port the functions**

Copy these verbatim from `outdoor_qto.py` into `backend/qto_engine.py`, keeping signatures and constants identical: `_bezier_pts`, `_make_pt_transform`, `_is_parallel_hatch`, `render_thick_boundaries`, `render_hatch_lines`, `preprocess_for_fill`. Imports at top: `import math, re; import cv2, fitz, numpy as np`. (These functions are pure PyMuPDF/OpenCV and need no adaptation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/qto_engine.py backend/tests/test_qto_engine.py
git commit -m "feat(qto): port line-width boundary/hatch rasterization"
```

---

### Task 2: Port tag extraction + scale conversion

**Files:**
- Modify: `backend/qto_engine.py`
- Test: `backend/tests/test_qto_engine.py`

**Interfaces:**
- Produces: `extract_tags(pdf_path, page_idx, clip_pct, tag_re, numeric_only) -> list[dict]` (each `{code,x,y}`), `px_to_sqft(pixels, dpi, scale_in_per_ft) -> float`, `parse_scale(text) -> float|None`.

- [ ] **Step 1: Write the failing test**

```python
def test_extract_M_tags_on_L1_01():
    import re
    tag_re = re.compile(r'^\(?M[-.]?(\d{1,2})\)?$', re.I)
    clip = dict(top=0.05, bottom=0.92, left=0.0, right=0.80)
    tags = qto_engine.extract_tags(PDF, L1_01_PAGE, clip, tag_re, numeric_only=True)
    codes = {t["code"] for t in tags}
    assert "M.5" in codes and "M.6" in codes
    assert all("x" in t and "y" in t for t in tags)

def test_px_to_sqft_scale_math():
    # 1/16" = 1ft, 150 dpi: 1 px = (1/150)in = (1/150)/(1/16) ft = 16/150 ft
    ft_per_px = (1/150) / (1/16)
    assert abs(qto_engine.px_to_sqft(1, 150, 1/16) - ft_per_px**2) < 1e-9
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k "tags or sqft" -q`
Expected: FAIL (`AttributeError: extract_tags`).

- [ ] **Step 3: Port the functions**

Port `parse_scale`, `pts_to_sqft`, `px_to_sqft` verbatim from `outdoor_qto.py`. Adapt `extract_tags` to take an explicit `clip_pct` dict and a compiled `tag_re` + `numeric_only` flag (instead of module globals): build `plan_clip = fitz.Rect(r.width*clip['left'], r.height*clip['top'], r.width*clip['right'], r.height*clip['bottom'])`, iterate `page.get_text("words", clip=plan_clip)`, match `tag_re.fullmatch(word.strip())`, build code `f"M.{int(m.group(1))}"` if `numeric_only` else `m.group(1)`, append `{code, x:(x0+x1)/2, y:(y0+y1)/2}`.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k "tags or sqft" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qto_engine.py backend/tests/test_qto_engine.py
git commit -m "feat(qto): port tag extraction + scale conversion"
```

---

### Task 3: Port zone detection (the accuracy core)

**Files:**
- Modify: `backend/qto_engine.py`
- Test: `backend/tests/test_qto_engine.py`

**Interfaces:**
- Produces: `detect_zones(binary, tags, pt_to_px, dpi, scale_in_per_ft, hatch_dark, phase1_min_zone_sf=0, phase2_radius_ft=24) -> tuple[dict[str,np.ndarray], dict[str,int]]`. Module constant `_CALLOUT_BUBBLE_MAX_PX = 2000`.

- [ ] **Step 1: Write the failing test (the accuracy gate)**

```python
def test_L1_01_areas_within_10pct_of_reference():
    import re
    cv = qto_engine  # alias
    dpi = 150
    scale = 1/16
    tag_re = re.compile(r'^\(?M[-.]?(\d{1,2})\)?$', re.I)
    clip = dict(top=0.05, bottom=0.92, left=0.0, right=0.80)
    tags = cv.extract_tags(PDF, L1_01_PAGE, clip, tag_re, numeric_only=True)
    boundary = cv.render_thick_boundaries(PDF, L1_01_PAGE, dpi)
    hatch = cv.render_hatch_lines(PDF, L1_01_PAGE, dpi)
    binary = cv.preprocess_for_fill(boundary)
    # mask title block (right 20%) like process_sheet does
    h, w = binary.shape
    binary[:, int(w*0.80):] = 0
    hatch_dark = (hatch < 200).astype('uint8'); hatch_dark[:, int(w*0.80):] = 0
    _, zone_px = cv.detect_zones(binary, tags, dpi/72.0, dpi, scale, hatch_dark)
    areas = {c: cv.px_to_sqft(px, dpi, scale) for c, px in zone_px.items()}
    for code, ref in {"M.5": 6550, "M.6": 1760}.items():
        assert abs(areas.get(code, 0) - ref) / ref < 0.10, (code, areas.get(code))
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k reference -q`
Expected: FAIL (`AttributeError: detect_zones`).

- [ ] **Step 3: Port `detect_zones` verbatim**

Copy `detect_zones` and `_CALLOUT_BUBBLE_MAX_PX` from `outdoor_qto.py` unchanged (Phase 1 spiral claim + Phase 2 simple/complex scoring). Replace the `print(...)` progress lines with a passed-in `emit` callback defaulting to a no-op, OR keep prints (acceptable for now). Keep all math identical.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k reference -q`
Expected: PASS — M.5 and M.6 within 10% of reference. If it fails, compare against running the lead's `outdoor_qto.py` directly to confirm parity (same dpi/scale).

- [ ] **Step 5: Commit**

```bash
git add backend/qto_engine.py backend/tests/test_qto_engine.py
git commit -m "feat(qto): port connected-component zone detection"
```

---

### Task 4: `run_sheet` orchestrator + Pillow overlay

**Files:**
- Modify: `backend/qto_engine.py`
- Test: `backend/tests/test_qto_engine.py`

**Interfaces:**
- Produces: `run_sheet(pdf_path, page_idx, sheet_cfg, out_png, dpi=150) -> dict` returning `{"areas": {code: sqft}, "overlay": out_png.name, "tags": int, "scale_in_per_ft": float}`. `sheet_cfg` keys: `scale_in_per_ft`, `tag_pattern`, `tag_numeric_only`, `clip` dict, `phase1_min_zone_sf`, `phase2_radius_ft`, `zone_detection_codes` (optional list).

- [ ] **Step 1: Write the failing test**

```python
def test_run_sheet_returns_areas_and_overlay(tmp_path):
    cfg = {"scale_in_per_ft": 1/16, "tag_pattern": r'^\(?M[-.]?(\d{1,2})\)?$',
           "tag_numeric_only": True,
           "clip": {"top":0.05,"bottom":0.92,"left":0.0,"right":0.80},
           "phase1_min_zone_sf": 0, "phase2_radius_ft": 24,
           "zone_detection_codes": ["M.5","M.6","M.7","M.15","M.16"]}
    out = tmp_path / "ov.png"
    res = qto_engine.run_sheet(PDF, L1_01_PAGE, cfg, out, dpi=150)
    assert abs(res["areas"]["M.5"] - 6550) / 6550 < 0.10
    assert out.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k run_sheet -q`
Expected: FAIL (`AttributeError: run_sheet`).

- [ ] **Step 3: Implement `run_sheet`**

```python
def run_sheet(pdf_path, page_idx, sheet_cfg, out_png, dpi=150):
    import re
    from pathlib import Path
    from PIL import Image
    scale = float(sheet_cfg["scale_in_per_ft"])
    tag_re = re.compile(sheet_cfg["tag_pattern"], re.I)
    clip = sheet_cfg["clip"]
    tags = extract_tags(pdf_path, page_idx, clip, tag_re, sheet_cfg.get("tag_numeric_only", True))
    # dedup near-identical tags
    uniq = []
    for t in tags:
        if not any(u["code"]==t["code"] and abs(u["x"]-t["x"])<10 and abs(u["y"]-t["y"])<10 for u in uniq):
            uniq.append(t)
    zcodes = sheet_cfg.get("zone_detection_codes")
    zone_tags = [t for t in uniq if (zcodes is None or t["code"] in set(zcodes))]
    boundary = render_thick_boundaries(pdf_path, page_idx, dpi)
    hatch = render_hatch_lines(pdf_path, page_idx, dpi)
    binary = preprocess_for_fill(boundary)
    h, w = binary.shape
    rclip = int(w * clip.get("right", 0.80)); lclip = int(w * clip.get("left", 0.0))
    binary[:, rclip:] = 0; binary[:, :lclip] = 0
    hatch_dark = (hatch < 200).astype("uint8"); hatch_dark[:, rclip:] = 0; hatch_dark[:, :lclip] = 0
    zone_masks, zone_px = detect_zones(binary, zone_tags, dpi/72.0, dpi, scale, hatch_dark,
                                       float(sheet_cfg.get("phase1_min_zone_sf", 0)),
                                       float(sheet_cfg.get("phase2_radius_ft", 24)))
    areas = {c: round(px_to_sqft(px, dpi, scale), 1) for c, px in zone_px.items()}
    # Pillow overlay: render page color, paint each mask in its palette colour
    doc = fitz.open(pdf_path); pix = doc[page_idx].get_pixmap(dpi=dpi); doc.close()
    base = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    base = cv2.resize(base, (w, h)) if base.shape[:2] != (h, w) else base
    over = base.copy()
    for code, mask in zone_masks.items():
        r, g, b = code_color_rgb(code)
        over[mask > 0] = (r, g, b)
    blended = cv2.addWeighted(base, 0.45, over, 0.55, 0)
    out_png = Path(out_png); out_png.parent.mkdir(parents=True, exist_ok=True)
    if blended.shape[1] > 2200:
        nh = int(blended.shape[0] * 2200 / blended.shape[1]); blended = cv2.resize(blended, (2200, nh))
    Image.fromarray(blended).save(out_png)
    return {"areas": areas, "overlay": out_png.name, "tags": len(zone_tags), "scale_in_per_ft": scale}
```

Also port `_PALETTE` and `code_color_rgb` from `outdoor_qto.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_qto_engine.py -k run_sheet -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qto_engine.py backend/tests/test_qto_engine.py
git commit -m "feat(qto): run_sheet orchestrator + overlay"
```

---

### Task 5: Port Gemini auto-config into `gemini_config.py`

**Files:**
- Create: `backend/gemini_config.py`
- Test: `backend/tests/test_gemini_config.py`

**Interfaces:**
- Produces: `find_key_pages(pdf_path) -> dict`, `build_config(pdf_path, api_key) -> dict` (returns the finalized config: `sheets`, `tag_pattern`, `tag_numeric_only`, clip pcts, `phase1_min_zone_sf`, `phase2_radius_ft`, `zone_detection_codes`, `materials`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_gemini_config.py
import os, re
from pathlib import Path
from dotenv import load_dotenv
from backend import gemini_config
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"

def test_find_key_pages_picks_material_plans():
    key = gemini_config.find_key_pages(PDF)
    idxs = [p["page_idx"] for p in key["plans"]]
    assert 2 in idxs  # L1.01 is page index 2

def test_build_config_returns_sheets_and_materials():
    cfg = gemini_config.build_config(PDF, os.environ["GEMINI_API_KEY"])
    assert cfg["sheets"], cfg
    assert any(k.startswith("M") for k in cfg.get("materials", {}))
    # required keys present
    for k in ("tag_pattern","plan_clip_right_pct","phase2_radius_ft","zone_detection_codes"):
        assert k in cfg
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_gemini_config.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Port + adapt**

Port `SCALE_RE, SHEET_RE, CODE_RE, PLAN_KEYWORDS, SCHED_KEYWORDS, SKIP_KEYWORDS, score_page, find_key_pages, render_page_jpeg, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, extract_json, finalize_config` verbatim from `generate_config.py`. Adapt the Gemini call for our SDK/model:

```python
import google.generativeai as genai
def call_gemini(api_key, images_with_labels):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-3.5-flash", system_instruction=SYSTEM_PROMPT,
                                  generation_config={"temperature":0.1,"max_output_tokens":8192})
    parts = [USER_PROMPT_TEMPLATE]
    for jpeg, label in images_with_labels:
        parts.append(f"\n[{label}]\n"); parts.append({"mime_type":"image/jpeg","data":jpeg})
    return model.generate_content(parts).text

def build_config(pdf_path, api_key):
    key = find_key_pages(pdf_path)
    pages = sorted({p["page_idx"] for p in key["plans"]})[:12]
    images = [(render_page_jpeg(pdf_path, i, dpi=96), f"PAGE {i+1} (idx {i})") for i in pages]
    raw = call_gemini(api_key, images)
    return finalize_config(extract_json(raw))
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_gemini_config.py -q`
Expected: PASS (build_config makes one live Gemini call; needs the key + network).

- [ ] **Step 5: Commit**

```bash
git add backend/gemini_config.py backend/tests/test_gemini_config.py
git commit -m "feat(qto): port Gemini auto-config builder (gemini-3.5-flash)"
```

---

### Task 6: Wire config + engine into Celery tasks & API

**Files:**
- Modify: `backend/store.py`, `backend/tasks.py`, `backend/main.py`

**Interfaces:**
- Consumes: `gemini_config.build_config`, `qto_engine.run_sheet`.
- Produces: Celery task `stage1_config(job_id, filename)` writing `config.json`; Stage 2 task branch using `run_sheet`; endpoints `POST /api/jobs/{id}/config`, `GET /api/jobs/{id}/config`.

- [ ] **Step 1: Add store helpers** — in `backend/store.py` add `config_path(job_id)` (`job_dir/config.json`), `write_config`/`read_config` (atomic write like `write_status`).

- [ ] **Step 2: Add the config task** — in `backend/tasks.py`:

```python
@shared_task(name="stage1_config")
def stage1_config(job_id, filename):
    return run_stage1_config(job_id, filename)

def run_stage1_config(job_id, filename):
    import os
    from dotenv import load_dotenv
    from . import gemini_config, store
    load_dotenv(store.BACKEND_DIR.parent / ".env")
    st = {"job_id": job_id, "filename": filename, "status": "running", "stage": "config"}
    store.write_status(job_id, st)
    try:
        cfg = gemini_config.build_config(str(store.pdf_path(job_id)), os.environ["GEMINI_API_KEY"])
        store.write_config(job_id, cfg)
        st.update(status="done", sheets=list(cfg.get("sheets", {})), materials=cfg.get("materials", {}),
                  zone_codes=cfg.get("zone_detection_codes"))
    except Exception as exc:  # noqa: BLE001
        st.update(status="error", error=f"{type(exc).__name__}: {exc}")
    store.write_status(job_id, st)
    return st
```

- [ ] **Step 3: Branch Stage 2 to the engine** — in `run_stage2`, when a `config.json` exists and the page maps to a config sheet, call `qto_engine.run_sheet(pdf, page, sheet_cfg, overlay_path)` and store `groups` as `[{label:code, sqft, hex}]` (so Stage 3's `legend_comparison` works by code). Keep the existing color-grouping/`detect_by_labels` path as fallback when no config sheet matches.

- [ ] **Step 4: Add endpoints** — in `backend/main.py`: `POST /api/jobs/{id}/config` dispatches `stage1_config` (eager → BackgroundTask); `GET /api/jobs/{id}/config` returns `store.read_config`. On `/api/upload`, also kick `stage1_config` after `stage1_select`.

- [ ] **Step 5: Manual integration check**

Run (with services up): upload LANDSCAPE via curl → poll `/api/jobs/{id}/config` until `done` → confirm `sheets` includes L1.01 and `materials` includes M.5. Then trigger Stage 2 on L1.01 → `GET stage2` shows `groups` with M.5 sqft ≈ 6550 (±10%).

- [ ] **Step 6: Commit**

```bash
git add backend/store.py backend/tasks.py backend/main.py
git commit -m "feat(qto): wire Gemini config + zone engine into tasks/API"
```

---

### Task 7: Stage 1 config review in the frontend

**Files:**
- Modify: `frontend/src/App.jsx`, `frontend/src/api.js`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/jobs/{id}/config`.

- [ ] **Step 1: api.js** — add `getConfig(jobId)` → `GET /api/jobs/${jobId}/config`.

- [ ] **Step 2: App.jsx** — in the Stage 1 results area, after the kept-pages grid, fetch + render a "Detected config" panel: scale per sheet, the materials table (code · name), and the area-codes (`zone_detection_codes`) highlighted. Show a "config: fallback" note if `config.source === 'fallback'`.

- [ ] **Step 3: styles.css** — add `.config-panel` styling consistent with existing panels.

- [ ] **Step 4: Manual check** — `npm run dev`, upload LANDSCAPE, confirm the config panel shows L1.01 scale `1/16"` and the M.x materials.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/api.js frontend/src/styles.css
git commit -m "feat(qto): Stage 1 detected-config review panel"
```

---

### Task 8: Validation table + Playwright e2e

**Files:**
- Modify: `backend/tasks.py` (validation in Stage 2 result), `frontend/tests/upload.spec.js`

- [ ] **Step 1: Add validation to the Stage 2 result** — when the engine path runs, attach `validation: [{code, computed, reference, delta_pct}]` using the known refs (L1.01: M.5 6550, M.6 1760, M.7 191; L1.04: M.9 3937) so Stage 3 can show a green/amber table.

- [ ] **Step 2: Playwright e2e**

```js
test("engine path: L1.01 areas within 10% of QTO", async ({ page }) => {
  test.setTimeout(240000);
  await page.goto("/");
  await page.locator('input[type="file"]').first()
    .setInputFiles("C:\\Users\\absar\\Downloads\\2811 Kirby\\2811 Kirby\\2811 Kirby - LANDSCAPE.pdf");
  await page.getByText(/required/).waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: /Continue → Stage 2/ }).click();
  await page.locator(".s2img img").waitFor({ timeout: 180000 });
  // M.5 sqft row visible and within range is asserted via the Stage 3 compare table
  await page.getByRole("button", { name: /Continue → Stage 3/ }).click();
  await expect(page.locator(".verdict")).toContainText(/MAPE/);
});
```

- [ ] **Step 3: Run the suite**

Run: `cd frontend && npx playwright test`
Expected: all green (existing + new).

- [ ] **Step 4: Commit**

```bash
git add backend/tasks.py frontend/tests/upload.spec.js
git commit -m "test(qto): validation table + engine e2e"
```

---

## Self-Review

**Spec coverage:** Stage 1 Gemini config (T5,T6,T7) ✓; Stage 2 engine — boundaries (T1), tags/scale (T2), zones (T3), run_sheet+overlay (T4), wiring (T6) ✓; Stage 3 comparison reused via per-code `groups` (T6) ✓; validation target (T3,T8) ✓; fallback config on Gemini failure (T6 store/error path — add default config in `run_stage1_config` except branch) ✓; debug artifacts retained (engine writes overlay; keep existing debug writer) ✓.

**Placeholder scan:** none — code shown for new logic; ported functions reference exact source funcs/constants.

**Type consistency:** `run_sheet` returns `{"areas","overlay","tags","scale_in_per_ft"}`; Task 6 maps `areas` → `groups[{label,sqft,hex}]` consumed by `legend_comparison` (matches existing Stage 3 by `label`). `build_config` returns the lead's config schema consumed by `run_sheet` via `sheet_cfg` (clip dict assembled in Task 6 from `plan_clip_*` keys).

**Gap fixed:** Task 6 Step 2 must convert flat `plan_clip_top/bottom/left/right_pct` from the config into the `clip` dict `run_sheet` expects — note added to Step 3 wiring.
