"""Celery tasks — one per pipeline stage. Stage 1: required-page selection."""
from __future__ import annotations

import os

import cv2
import fitz
import numpy as np
from PIL import Image

from celery import shared_task

from . import (estimate_parse, gemini_config, pool_mode, qto_engine, selection,
               stage2, store)
from .stage2 import legend_comparison


def _is_pool_plan(page) -> bool:
    """Heuristic: a pool/spa PLAN sheet (where the pool body is drawn)."""
    t = page.get_text().upper()
    return ("POOL" in t) and ("PLAN" in t) and ("POOL SECTION" not in t[:200])


def _load_pool_targets(job_id: str) -> dict | None:
    """Pool/spa area targets from the OE estimate: per-job estimate.pdf, else an
    OE_ESTIMATE_PATH env fallback (for demos)."""
    ep = store.estimate_path(job_id)
    src = str(ep) if ep.exists() else os.environ.get("OE_ESTIMATE_PATH")
    if not src or not os.path.exists(src):
        return None
    t = estimate_parse.parse_pool_targets(src)
    return t or None


def _persist_masks(job_id: str, page: int, masks: dict, scale: float, dpi: int = 150) -> None:
    """Save per-code zone masks as one label image (pixel = code index) so a
    later click can identify & remove a region. Masks are non-overlapping."""
    if not masks:
        return
    codes = list(masks.keys())
    h, w = next(iter(masks.values())).shape
    label = np.zeros((h, w), np.uint8)
    for i, c in enumerate(codes):
        label[masks[c] > 0] = i + 1
    np.savez_compressed(store.masks_path(job_id, page), label=label,
                        codes=np.array(codes), scale=float(scale), dpi=int(dpi))

# QTO reference values (the lead's `known` dict + our QTO legend) for the
# engine-path validation table.
_KNOWN_REFS = {
    "L1.01": {"M.5": 6550, "M.6": 1760, "M.7": 191, "M.15": 369, "M.16": 414},
    "L1.02": {"M.11": 703, "M.12": 1090, "M.13": 554},
    "L1.04": {"M.9": 3937},
}


def _build_validation(sheet_id: str, areas: dict) -> list[dict]:
    refs = _KNOWN_REFS.get(sheet_id, {})
    rows = []
    for code, ref in refs.items():
        computed = areas.get(code)
        delta = round((computed - ref) / ref * 100, 1) if (computed and ref) else None
        rows.append({"code": code, "computed": computed, "reference": ref, "delta_pct": delta})
    return rows


@shared_task(name="stage1_config")
def stage1_config(job_id: str, filename: str) -> dict:
    """Gemini auto-config (Stage 1): build qto_config.json for the uploaded PDF."""
    return run_stage1_config(job_id, filename)


def run_stage1_config(job_id: str, filename: str) -> dict:
    import os
    from dotenv import load_dotenv
    load_dotenv(store.BACKEND_DIR.parent / ".env")
    st = {"job_id": job_id, "filename": filename, "status": "running", "stage": "config"}
    store.write_status(job_id, {**(store.read_status(job_id) or {}), "config_status": "running"})
    try:
        cfg = gemini_config.build_config(str(store.pdf_path(job_id)), os.environ["GEMINI_API_KEY"])
    except Exception as exc:  # noqa: BLE001 — fall back to an empty config, surface the error
        cfg = {"sheets": {}, "materials": {}, "source": "fallback",
               "error": f"{type(exc).__name__}: {exc}"}
    store.write_config(job_id, cfg)
    cur = store.read_status(job_id) or {}
    cur["config_status"] = "done"
    store.write_status(job_id, cur)
    st.update(status="done", sheets=list(cfg.get("sheets", {})), source=cfg.get("source"))
    return st


@shared_task(name="stage1_select")
def stage1_select(job_id: str, filename: str) -> dict:
    """Celery entry point — thin wrapper around run_stage1."""
    return run_stage1(job_id, filename)


def run_stage1(job_id: str, filename: str) -> dict:
    """Classify every page, render thumbnails for kept pages, persist the result.

    Plain function so it can run either as a Celery task (Redis) or in a FastAPI
    background thread (eager / no-Redis). Returns the dict written to status.json.
    """
    pdf = store.pdf_path(job_id)
    status = store.read_status(job_id) or {}
    status.update(job_id=job_id, filename=filename, status="running")
    store.write_status(job_id, status)

    try:
        results = selection.analyze_pdf(pdf)
        thumbs = store.thumbs_dir(job_id)
        for r in results:
            if r.keep:
                out = thumbs / f"p{r.index}.png"
                selection.render_thumb(pdf, r.index, out)
                r.thumb = f"thumbs/p{r.index}.png"

        kept = [r for r in results if r.keep]
        status.update(
            status="done",
            page_count=len(results),
            kept_count=len(kept),
            pool_style_count=sum(1 for r in results if r.pool_style),
            pages=[r.to_dict() for r in results],
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure to the client
        status.update(status="error", error=f"{type(exc).__name__}: {exc}")

    store.write_status(job_id, status)
    return status


@shared_task(name="stage2_detect")
def stage2_detect(job_id: str, page: int) -> dict:
    """Detect & color the surface regions on one page (Stage 2). Celery entry."""
    return run_stage2(job_id, page)


def run_stage2(job_id: str, page: int) -> dict:
    pdf = store.pdf_path(job_id)
    status = {"job_id": job_id, "page": page, "status": "running"}
    store.write_stage2(job_id, page, status)
    try:
        out = store.stage2_dir(job_id) / f"overlay_p{page}.png"
        cfg = store.read_config(job_id)
        sheet_cfg = store.sheet_cfg_for_page(cfg, page) if cfg else None

        # If the page already has human-marked colored surfaces (a QTO / colored
        # plan — pool, spa, planting, hardscape), extract those surfaces directly.
        # The line-width engine is only for RAW monochrome landscape material
        # plans where the colors don't exist yet.
        page_obj = _open_page(pdf, page)
        chromatic = stage2._page_is_chromatic(page_obj)
        pool_targets = _load_pool_targets(job_id)

        if not chromatic and pool_targets and _is_pool_plan(page_obj):
            # Pool mode: estimate-guided pool/spa surface detection (raw B&W pool
            # sheet, no tags). Scale auto-calibrated from the estimate targets.
            res = pool_mode.detect_pool(pdf, page, pool_targets, out, dpi=120)
            groups = [
                {"label": s["name"], "sqft": s["area_sf"], "regions": 1,
                 "perimeter_lf": s.get("perimeter_lf"),
                 "hex": "#%02x%02x%02x" % pool_mode._SURFACE_COLOR.get(
                     s["name"].upper(), pool_mode._DEFAULT_COLOR)}
                for s in res["surfaces"]
            ]
            status.update(
                status="done", overlay=f"overlay_p{page}.png", method="pool",
                scale_in_per_ft=res["scale_in_per_ft"],
                message=(f"Pool mode — {len(groups)} surfaces · estimate-guided "
                         f"(scale 1/{1/res['scale_in_per_ft']:.1f}\" = 1')"),
                groups=groups,
            )
        elif sheet_cfg and not chromatic:
            # Engine path: line-width zones + connected components (the lead's method)
            res = qto_engine.run_sheet(pdf, page, sheet_cfg, out)
            _persist_masks(job_id, page, res.get("masks", {}), res["scale_in_per_ft"])
            groups = [
                {"label": code, "sqft": sqft, "regions": 1,
                 "hex": "#%02x%02x%02x" % qto_engine.code_color_rgb(code)}
                for code, sqft in sorted(res["areas"].items(),
                                         key=lambda kv: -kv[1])
            ]
            sid = sheet_cfg["sheet_id"]
            status.update(
                status="done", overlay=f"overlay_p{page}.png", method="engine",
                sheet=sid, scale_in_per_ft=res["scale_in_per_ft"],
                message=(f"{sid} ({sheet_cfg['title']}) — {len(groups)} materials, "
                         f"scale 1\" = {1/res['scale_in_per_ft']:.0f}' · line-width zone engine"),
                groups=groups,
                comparison=legend_comparison(_open_page(pdf, page), groups),
                validation=_build_validation(sid, res["areas"]),
            )
        else:
            # Fallback: existing color-grouping / label-seeding
            res = stage2.detect_color_regions(pdf, page, out)
            status.update(status="done", overlay=f"overlay_p{page}.png", **{
                "vector": res["vector"], "message": res["message"], "groups": res["groups"],
                "comparison": res.get("comparison"),
            })
    except Exception as exc:  # noqa: BLE001
        status.update(status="error", error=f"{type(exc).__name__}: {exc}")
    store.write_stage2(job_id, page, status)
    return status


def _open_page(pdf_path, page: int):
    import fitz
    return fitz.open(pdf_path)[page]


def _load_label(job_id: str, page: int):
    z = np.load(store.masks_path(job_id, page), allow_pickle=False)
    return z["label"].copy(), [str(c) for c in z["codes"]], float(z["scale"]), int(z["dpi"])


def _save_label(path, label, codes, scale, dpi) -> None:
    np.savez_compressed(path, label=label, codes=np.array(codes), scale=scale, dpi=dpi)


def _apply_label(job_id, page, label, codes, scale, dpi, note) -> dict:
    """Recompute areas + masks from the edited label image, re-render the
    overlay, and update + return the Stage-2 status."""
    h, w = label.shape
    areas, masks = {}, {}
    for i, c in enumerate(codes):
        m = label == (i + 1)
        px = int(m.sum())
        if px > 0:
            areas[c] = round(qto_engine.px_to_sqft(px, dpi, scale), 1)
            masks[c] = m

    pdf = store.pdf_path(job_id)
    doc = fitz.open(pdf)
    pix = doc[page].get_pixmap(dpi=dpi)
    doc.close()
    base = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    if base.shape[:2] != (h, w):
        base = cv2.resize(base, (w, h))
    over = base.copy()
    for c, m in masks.items():
        over[m] = qto_engine.code_color_rgb(c)
    blended = cv2.addWeighted(base, 0.45, over, 0.55, 0)
    if blended.shape[1] > 2200:
        nh = int(blended.shape[0] * 2200 / blended.shape[1])
        blended = cv2.resize(blended, (2200, nh))
    Image.fromarray(blended).save(store.stage2_dir(job_id) / f"overlay_p{page}.png")

    status = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    groups = [{"label": c, "sqft": s, "regions": 1,
               "hex": "#%02x%02x%02x" % qto_engine.code_color_rgb(c)}
              for c, s in sorted(areas.items(), key=lambda kv: -kv[1])]
    status["groups"] = groups
    status["comparison"] = legend_comparison(_open_page(pdf, page), groups)
    if status.get("sheet"):
        status["validation"] = _build_validation(status["sheet"], areas)
    status["edit_note"] = note
    status["can_undo"] = store.masks_undo_path(job_id, page).exists()
    store.write_stage2(job_id, page, status)
    return status


def pick_region(job_id: str, page: int, fx: float, fy: float) -> dict:
    """Identify (but don't remove) the zone at the click — for select+confirm.
    Returns {code, area, bbox:[fx0,fy0,fx1,fy1]} or {code: None}."""
    if not store.masks_path(job_id, page).exists():
        return {"code": None}
    label, codes, scale, dpi = _load_label(job_id, page)
    h, w = label.shape
    mx = min(max(int(fx * w), 0), w - 1)
    my = min(max(int(fy * h), 0), h - 1)
    cidx = int(label[my, mx])
    if cidx == 0:
        return {"code": None}
    code = codes[cidx - 1]
    _n, lbl, stats, _c = cv2.connectedComponentsWithStats((label == cidx).astype(np.uint8), 8)
    comp = int(lbl[my, mx])
    x, y = int(stats[comp, cv2.CC_STAT_LEFT]), int(stats[comp, cv2.CC_STAT_TOP])
    cw, ch = int(stats[comp, cv2.CC_STAT_WIDTH]), int(stats[comp, cv2.CC_STAT_HEIGHT])
    area = qto_engine.px_to_sqft(int(stats[comp, cv2.CC_STAT_AREA]), dpi, scale)
    return {"code": code, "area": round(area, 1),
            "bbox": [x / w, y / h, (x + cw) / w, (y + ch) / h]}


def remove_region(job_id: str, page: int, fx: float, fy: float) -> dict:
    """Remove the single zone (connected component) clicked at (fx, fy)."""
    if not store.masks_path(job_id, page).exists():
        return store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    label, codes, scale, dpi = _load_label(job_id, page)
    h, w = label.shape
    mx = min(max(int(fx * w), 0), w - 1)
    my = min(max(int(fy * h), 0), h - 1)
    cidx = int(label[my, mx])
    if cidx == 0:
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = "Nothing to remove there."
        return s
    code = codes[cidx - 1]
    _n, lbl, stats, _c = cv2.connectedComponentsWithStats((label == cidx).astype(np.uint8), 8)
    comp = int(lbl[my, mx])
    removed_sf = qto_engine.px_to_sqft(int(stats[comp, cv2.CC_STAT_AREA]), dpi, scale)
    _save_label(store.masks_undo_path(job_id, page), label, codes, scale, dpi)  # undo backup
    label[lbl == comp] = 0
    _save_label(store.masks_path(job_id, page), label, codes, scale, dpi)
    return _apply_label(job_id, page, label, codes, scale, dpi,
                        f"Removed {removed_sf:.0f} sq ft from {code}.")


def remove_material(job_id: str, page: int, code: str) -> dict:
    """Remove ALL shaded zones of one material code."""
    if not store.masks_path(job_id, page).exists():
        return store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    label, codes, scale, dpi = _load_label(job_id, page)
    if code not in codes:
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = f"{code} not on this page."
        return s
    cidx = codes.index(code) + 1
    removed_sf = qto_engine.px_to_sqft(int((label == cidx).sum()), dpi, scale)
    _save_label(store.masks_undo_path(job_id, page), label, codes, scale, dpi)  # undo backup
    label[label == cidx] = 0
    _save_label(store.masks_path(job_id, page), label, codes, scale, dpi)
    return _apply_label(job_id, page, label, codes, scale, dpi,
                        f"Removed all of {code} ({removed_sf:.0f} sq ft).")


def remove_batch(job_id: str, page: int, points: list) -> dict:
    """Remove every zone hit by the list of clicks (multi-select delete) in one
    pass — one undo backup, one re-render."""
    if not store.masks_path(job_id, page).exists():
        return store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    label, codes, scale, dpi = _load_label(job_id, page)
    h, w = label.shape
    _save_label(store.masks_undo_path(job_id, page), label, codes, scale, dpi)  # undo backup
    to_zero = np.zeros_like(label, dtype=bool)
    n_zones = 0
    for pt in points:
        mx = min(max(int(float(pt["x"]) * w), 0), w - 1)
        my = min(max(int(float(pt["y"]) * h), 0), h - 1)
        cidx = int(label[my, mx])
        if cidx == 0:
            continue
        _n, lbl, _s, _c = cv2.connectedComponentsWithStats((label == cidx).astype(np.uint8), 8)
        comp = int(lbl[my, mx])
        sel = lbl == comp
        if not (to_zero & sel).any():
            n_zones += 1
        to_zero |= sel
    label[to_zero] = 0
    _save_label(store.masks_path(job_id, page), label, codes, scale, dpi)
    removed_sf = qto_engine.px_to_sqft(int(to_zero.sum()), dpi, scale)
    return _apply_label(job_id, page, label, codes, scale, dpi,
                        f"Removed {n_zones} zone(s) — {removed_sf:.0f} sq ft.")


def undo_last(job_id: str, page: int) -> dict:
    """Restore the label from the one-level undo backup."""
    up = store.masks_undo_path(job_id, page)
    if not up.exists():
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = "Nothing to undo."
        s["can_undo"] = False
        return s
    z = np.load(up, allow_pickle=False)
    label = z["label"].copy()
    codes = [str(c) for c in z["codes"]]
    scale, dpi = float(z["scale"]), int(z["dpi"])
    _save_label(store.masks_path(job_id, page), label, codes, scale, dpi)
    up.unlink()  # consume (single-level undo)
    return _apply_label(job_id, page, label, codes, scale, dpi, "Undid last removal.")
