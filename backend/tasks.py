"""Celery tasks — one per pipeline stage. Stage 1: required-page selection."""
from __future__ import annotations

import os

import cv2
import fitz
import numpy as np
from PIL import Image

from celery import shared_task

from . import (gemini_config, legend, pool_mode, pool_scope, qto_engine,
               selection, stage2, store, zone_filter, zones)
from .stage2 import legend_comparison


def _is_pool_plan(page) -> bool:
    """True only for pool/spa SURFACE PLAN sheets — not piping, equipment, or detail sheets."""
    t = page.get_text().upper()
    if not (("POOL" in t or "SPA" in t or "AQUATIC" in t) and "PLAN" in t):
        return False
    # Exclude sheets that show infrastructure or details, not plan-view surfaces
    EXCLUDE = ("PIPING PLAN", "EQUIPMENT PLAN", "EQUIPMENT ROOM", "RETURN PIPING",
               "SUPPLY PIPING", "POOL SECTION", "SPA SECTION", "HYDRO THERAPY",
               "HYDROTHERA", "POOL DETAILS", "SPA DETAILS", "POOL DETAIL",
               "SPA DETAIL", "CONSTRUCTION DETAILS")
    return not any(kw in t for kw in EXCLUDE)


def _persist_masks(job_id: str, page: int, masks: dict, scale: float, dpi: int = 150) -> None:
    """Save per-code zone masks as one label image (pixel = code index) and seed
    the per-zone DB rows (one row per connected component, with a stable id).
    The zones table is the source of truth for editing/rendering thereafter."""
    if not masks:
        store.replace_zones(job_id, page, [])
        return
    codes = list(masks.keys())
    h, w = next(iter(masks.values())).shape
    label = np.zeros((h, w), np.uint8)
    for i, c in enumerate(codes):
        label[masks[c] > 0] = i + 1
    np.savez_compressed(store.masks_path(job_id, page), label=label,
                        codes=np.array(codes), scale=float(scale), dpi=int(dpi))
    zlist = zones.extract_zones_from_label(label, codes, float(scale), int(dpi),
                                           source="engine")
    zlist = zone_filter.filter_false_positives(
        zlist,
        store.pdf_path(job_id),
        page,
        int(dpi),
        float(scale),
        os.environ.get("GEMINI_API_KEY"),
    )
    store.replace_zones(job_id, page, zlist)

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


def _auto_sheet_cfg(pdf, page: int, page_obj) -> dict:
    """Default per-sheet config for a B&W material plan that has no reviewed
    config — so the lead's line-width zone engine still runs (crisp zones)
    instead of the rounded label-seeding fallback. Scale is read from the sheet's
    printed scale note; the material tag family is auto-detected (Kirby `M.x`,
    Pelican `A#`, …) so the engine generalizes beyond the hardcoded `M` family."""
    fpi = stage2.sheet_feet_per_inch(page_obj, default=16.0)
    clip = {"top": 0.05, "bottom": 0.92, "left": 0.0, "right": 0.80}
    tcfg = legend.tag_config(pdf, page, clip)
    if tcfg:
        tag_pattern, title = tcfg["tag_pattern"], f"Material Plan ({tcfg['family']})"
    else:
        tag_pattern, title = r"^\(?(M[-.]?\d{1,2})\)?$", "Material Plan"
    return {
        "sheet_id": "AUTO",
        "title": title,
        "scale_in_per_ft": 1.0 / fpi,
        "tag_pattern": tag_pattern,
        "tag_numeric_only": False,
        "clip": clip,
        "phase1_min_zone_sf": 0,
        "phase2_radius_ft": 24,
    }


def _has_material_tags(pdf, page: int, cfg: dict) -> bool:
    """True if the page carries M.x material callout tags (what the engine needs)."""
    import re
    tag_re = re.compile(cfg["tag_pattern"], re.I)
    tags = qto_engine.extract_tags(pdf, page, cfg["clip"], tag_re,
                                   cfg.get("tag_numeric_only", True))
    return len(tags) >= 2


def _compute_takeoff(job_id, pdf, page: int, groups: list, scale: float) -> list:
    """Area + linear + count takeoff for the page. Reuses the engine's area totals
    and vision-reads the legend for the linear/count split (walls, trees, etc.).
    Best-effort: never let the takeoff extras break Stage 2."""
    try:
        from . import takeoff
        area_by_code = {g["label"]: g.get("sqft", 0.0)
                        for g in (groups or []) if g.get("label")}
        items = takeoff.build_takeoff(str(pdf), page, use_vision=True,
                                      scale_in_per_ft=scale, areas=area_by_code)
    except Exception:  # noqa: BLE001
        items = []
    # Visual counts via gemini-3.1-pro-preview: trees/pools/spas the geometric
    # engine can't count. A page dense with trees is a PLANTING plan -> read the
    # schedule and produce per-species counts instead of the generic "Trees" row.
    try:
        from . import visual_detect, planting
        # Planting plan? Gate on the plant schedule + plant labels on this page
        # (deterministic) — per-species counts. Else generic trees/pools/spas.
        prows = []
        sp = planting.find_schedule_page(str(pdf))
        if sp is not None:
            sched = planting.read_schedule(str(pdf), sp)
            prows = planting.page_count_rows(str(pdf), page, sched)
        if prows:
            items = items + prows
            # color the plants on the overlay automatically (during extraction)
            try:
                out = store.stage2_dir(job_id) / f"overlay_p{page}.png"
                planting.render_planting_overlay(str(pdf), page, sched, str(out))
            except Exception:  # noqa: BLE001
                pass
        else:
            rows, _anns = visual_detect.count_takeoff_rows(str(pdf), page)
            items = items + rows
    except Exception:  # noqa: BLE001
        pass
    return items


@shared_task(name="stage2_detect")
def stage2_detect(job_id: str, page: int, force: bool = False) -> dict:
    """Detect & color the surface regions on one page (Stage 2). Celery entry."""
    return run_stage2(job_id, page, force)


def run_stage2(job_id: str, page: int, force: bool = False) -> dict:
    # Resume-safe: never re-detect a page that's already done OR currently being
    # detected (queued/running) unless forced — prevents the batch and a manual
    # trigger racing on the same page.
    if not force:
        existing = store.read_stage2(job_id, page)
        if existing and existing.get("status") in ("done", "running", "queued"):
            return existing
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

        # A B&W material sheet with M.x tags but no reviewed config: build a
        # default config so the lead's line-width engine runs (crisp zones)
        # rather than the rounded label-seeding fallback.
        auto_cfg = None
        if not chromatic and not sheet_cfg:
            _ac = _auto_sheet_cfg(pdf, page, page_obj)
            if _has_material_tags(pdf, page, _ac):
                auto_cfg = _ac

        if _is_pool_plan(page_obj):
            # Pool mode: vision-guided pool/spa surface detection.
            # Gemini reads the zone labels from the drawing itself; scale is
            # parsed from the title block. No estimate PDF required.
            res = pool_mode.detect_pool(
                pdf, page, out, dpi=120,
                api_key=os.environ.get("GEMINI_API_KEY"),
            )
            store.replace_zones(job_id, page, res.get("zones", []))
            groups = zones.groups_from_zones(res.get("zones", []))
            scale = res["scale_in_per_ft"]
            status.update(
                status="done", overlay=f"overlay_p{page}.png", method="pool",
                scale_in_per_ft=scale,
                message=(f"Pool mode — {len(groups)} surfaces · vision-guided "
                         f"(scale 1/{1/scale:.2g}\" = 1')"),
                groups=groups,
            )
        elif not chromatic and (sheet_cfg or auto_cfg):
            # Engine path: line-width zones + connected components (the lead's method).
            # Use the reviewed per-sheet config when available, else the auto config.
            # Seed per-zone rows, then render the overlay + totals FROM the zones so
            # the initial view matches the (zone-based) editing model exactly.
            cfg2 = sheet_cfg or auto_cfg
            res = qto_engine.run_sheet(pdf, page, cfg2, out)
            _persist_masks(job_id, page, res.get("masks", {}), res["scale_in_per_ft"])
            active = store.active_zones(job_id, page)
            zres = zones.render_from_zones(pdf, page, active, out)
            groups = zres["groups"]
            sid = cfg2["sheet_id"]
            auto = sheet_cfg is None
            status.update(
                status="done", overlay=f"overlay_p{page}.png", method="engine",
                sheet=sid, scale_in_per_ft=res["scale_in_per_ft"],
                message=(f"{sid} ({cfg2['title']}) — {len(groups)} materials, "
                         f"scale 1\" = {1/res['scale_in_per_ft']:.0f}'"
                         f"{' (auto)' if auto else ''} · line-width zone engine"),
                groups=groups,
                comparison=legend_comparison(_open_page(pdf, page), groups),
                validation=_build_validation(sid, res["areas"]),
            )
            status["takeoff"] = _compute_takeoff(job_id, pdf, page, groups, res["scale_in_per_ft"])
        else:
            # Fallback: existing color-grouping / label-seeding
            res = stage2.detect_color_regions(pdf, page, out)
            store.replace_zones(job_id, page, [])  # color path not zone-addressable yet
            status.update(status="done", overlay=f"overlay_p{page}.png", **{
                "vector": res["vector"], "message": res["message"], "groups": res["groups"],
                "comparison": res.get("comparison"),
            })
        # Any completed path that didn't set it (pool, color): add the
        # area+linear+count takeoff so walls/trees/counts show too.
        if status.get("status") == "done" and "takeoff" not in status:
            status["takeoff"] = _compute_takeoff(
                job_id, pdf, page, status.get("groups", []),
                status.get("scale_in_per_ft", 1.0 / 16))
    except Exception as exc:  # noqa: BLE001
        status.update(status="error", error=f"{type(exc).__name__}: {exc}")
    store.write_stage2(job_id, page, status)
    return status


@shared_task(name="stage2_detect_all")
def stage2_detect_all(job_id: str) -> dict:
    """Celery entry point for batch detection of every kept page."""
    return detect_kept_pages(job_id)


def detect_kept_pages(job_id: str) -> dict:
    """Detect every KEPT page of the job, sequentially. Resume-safe: run_stage2
    returns the saved result for an already-done page, so re-runs are cheap."""
    status = store.read_status(job_id) or {}
    detected = skipped = 0
    for p in status.get("pages", []):
        if not p.get("keep"):
            continue
        page = int(p["index"])
        before = store.read_stage2(job_id, page)
        if before and before.get("status") == "done":
            skipped += 1
            continue
        run_stage2(job_id, page)
        detected += 1
    return {"detected": detected, "skipped": skipped}


def _open_page(pdf_path, page: int):
    import fitz
    return fitz.open(pdf_path)[page]


def _page_dims(job_id: str, page: int) -> tuple[float, float]:
    """Base-page size in PDF points (for fractional-click -> point hit testing)."""
    doc = fitz.open(store.pdf_path(job_id))
    r = doc[page].rect
    doc.close()
    return r.width, r.height


def _rerender_from_zones(job_id: str, page: int, note: str | None = None,
                         deleted_ids: list[str] | None = None) -> dict:
    """Rebuild the overlay + per-code totals from the page's ACTIVE zones and
    refresh the Stage-2 status. The zones table is the single source of truth, so
    every edit (click or delete-by-id) funnels through here."""
    pdf = store.pdf_path(job_id)
    active = store.active_zones(job_id, page)
    out = store.stage2_dir(job_id) / f"overlay_p{page}.png"
    status = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    status["status"] = "done"
    try:
        # re-render the overlay + totals from the base plan and active zones
        res = zones.render_from_zones(pdf, page, active, out)
        status["overlay"] = f"overlay_p{page}.png"
        status["comparison"] = legend_comparison(_open_page(pdf, page), res["groups"])
        groups = res["groups"]
    except Exception:  # noqa: BLE001 — base PDF missing/unreadable: still update totals
        groups = zones.groups_from_zones(active)
    status["groups"] = groups
    if status.get("sheet"):
        areas = {g["label"]: g["sqft"] for g in groups}
        status["validation"] = _build_validation(status["sheet"], areas)
    if note is not None:
        status["edit_note"] = note
    if deleted_ids is not None:
        status["last_deleted_ids"] = deleted_ids
    status["can_undo"] = bool(status.get("last_deleted_ids"))
    store.write_stage2(job_id, page, status)
    return status


def _zone_at(job_id: str, page: int, fx: float, fy: float) -> dict | None:
    """The smallest active zone whose polygon contains the fractional click."""
    active = store.active_zones(job_id, page)
    if not active:
        return None
    W, H = _page_dims(job_id, page)
    px, py = fx * W, fy * H
    hit, hit_area = None, float("inf")
    for z in active:
        for poly in z.get("geometry") or []:
            cont = np.array(poly, dtype=np.float32)
            if len(cont) >= 3 and cv2.pointPolygonTest(cont, (float(px), float(py)), False) >= 0:
                a = z.get("area_sqft") or 0.0
                if a < hit_area:
                    hit, hit_area = z, a
                break
    return hit


def pick_region(job_id: str, page: int, fx: float, fy: float) -> dict:
    """Identify (but don't remove) the zone at the click — returns its id, code,
    area and bbox, or {id: None} when the click is on no zone."""
    z = _zone_at(job_id, page, fx, fy)
    if z is None:
        return {"id": None, "code": None}
    return {"id": z["id"], "code": z["code"], "area": z.get("area_sqft"),
            "bbox": z.get("bbox")}


def remove_region(job_id: str, page: int, fx: float, fy: float) -> dict:
    """Soft-delete the single zone clicked at (fx, fy)."""
    z = _zone_at(job_id, page, fx, fy)
    if z is None:
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = "Nothing to remove there."
        return s
    store.set_zone_status(z["id"], "deleted")
    return _rerender_from_zones(
        job_id, page, f"Removed {z.get('area_sqft') or 0:.0f} sq ft from {z['code']}.",
        [z["id"]])


def remove_material(job_id: str, page: int, code: str) -> dict:
    """Soft-delete ALL active zones of one material code."""
    ids = store.set_zones_status_by_code(job_id, page, code, "deleted")
    if not ids:
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = f"{code} not on this page."
        return s
    return _rerender_from_zones(job_id, page,
                                f"Removed all of {code} ({len(ids)} zone(s)).", ids)


def remove_batch(job_id: str, page: int, points: list) -> dict:
    """Soft-delete every zone hit by the list of clicks (multi-select)."""
    ids: list[str] = []
    for pt in points:
        z = _zone_at(job_id, page, float(pt["x"]), float(pt["y"]))
        if z is not None and z["id"] not in ids:
            ids.append(z["id"])
    for zid in ids:
        store.set_zone_status(zid, "deleted")
    return _rerender_from_zones(job_id, page, f"Removed {len(ids)} zone(s).", ids)


def delete_zone(job_id: str, zone_id: str) -> dict:
    """Soft-delete one zone by id (the addressable delete)."""
    info = store.set_zone_status(zone_id, "deleted")
    if info is None:
        return {"error": "unknown zone id"}
    return _rerender_from_zones(info["job_id"], info["page"],
                                f"Removed zone {zone_id[:8]} ({info['code']}).", [zone_id])


def delete_zones(job_id: str, page: int, ids: list[str]) -> dict:
    """Soft-delete a set of zones by id in one pass, then re-render once."""
    deleted = []
    for zid in ids:
        if store.set_zone_status(zid, "deleted") is not None:
            deleted.append(zid)
    if not deleted:
        s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
        s["edit_note"] = "Nothing to remove."
        return s
    return _rerender_from_zones(job_id, page, f"Removed {len(deleted)} zone(s).", deleted)


def restore_zone(job_id: str, zone_id: str) -> dict:
    """Restore a soft-deleted zone by id."""
    info = store.set_zone_status(zone_id, "active")
    if info is None:
        return {"error": "unknown zone id"}
    return _rerender_from_zones(info["job_id"], info["page"],
                                f"Restored zone {zone_id[:8]} ({info['code']}).")


def undo_last(job_id: str, page: int) -> dict:
    """Restore the zones removed in the last delete (one-level undo)."""
    s = store.read_stage2(job_id, page) or {"job_id": job_id, "page": page}
    ids = s.get("last_deleted_ids") or []
    if not ids:
        s["edit_note"] = "Nothing to undo."
        s["can_undo"] = False
        return s
    for zid in ids:
        store.set_zone_status(zid, "active")
    return _rerender_from_zones(job_id, page, "Undid last removal.", [])
