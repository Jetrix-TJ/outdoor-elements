"""Job store.

Structured per-job / per-stage *data* lives in Postgres (see db.py): job status
+ page selection, Gemini config, Stage-2 results, and pricing. Large binaries —
the uploaded PDF, thumbnails, overlay PNGs, and the edit mask .npz — stay on the
filesystem under jobs/<job_id>/, referenced by job_id.

The read_*/write_* interface is unchanged from the old JSON-file store, so
tasks.py and main.py are unaffected by the move to Postgres.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from . import db

BACKEND_DIR = Path(__file__).resolve().parent
JOBS_DIR = BACKEND_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


# ── filesystem: binaries only (PDF / thumbs / overlays / masks) ──────────────
def job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_path(job_id: str) -> Path:
    return job_dir(job_id) / "upload.pdf"


def thumbs_dir(job_id: str) -> Path:
    d = job_dir(job_id) / "thumbs"
    d.mkdir(exist_ok=True)
    return d


def previews_dir(job_id: str) -> Path:
    d = job_dir(job_id) / "previews"
    d.mkdir(exist_ok=True)
    return d


def stage2_dir(job_id: str) -> Path:
    d = job_dir(job_id) / "stage2"
    d.mkdir(exist_ok=True)
    return d


def masks_path(job_id: str, page: int) -> Path:
    """Persisted per-code zone masks for a Stage-2 page (for click-to-remove)."""
    return stage2_dir(job_id) / f"masks_p{page}.npz"


def masks_undo_path(job_id: str, page: int) -> Path:
    """One-level undo backup of the label image, written before each edit."""
    return stage2_dir(job_id) / f"masks_undo_p{page}.npz"


def estimate_path(job_id: str) -> Path:
    return job_dir(job_id) / "estimate.pdf"


# ── Postgres: structured data ────────────────────────────────────────────────
def _get_or_create(s, job_id: str) -> "db.Job":
    job = s.get(db.Job, job_id)
    if job is None:
        job = db.Job(job_id=job_id)
        s.add(job)
        s.flush()
    return job


def write_status(job_id: str, data: dict) -> None:
    with db.session() as s:
        job = _get_or_create(s, job_id)
        job.status = data
        job.filename = data.get("filename") or job.filename


def read_status(job_id: str) -> dict | None:
    with db.session() as s:
        job = s.get(db.Job, job_id)
        return job.status if job else None


def write_config(job_id: str, cfg: dict) -> None:
    with db.session() as s:
        _get_or_create(s, job_id).config = cfg


def read_config(job_id: str) -> dict | None:
    with db.session() as s:
        job = s.get(db.Job, job_id)
        return job.config if job else None


def write_prices(job_id: str, rates: dict) -> None:
    with db.session() as s:
        _get_or_create(s, job_id).prices = rates


def read_prices(job_id: str) -> dict:
    with db.session() as s:
        job = s.get(db.Job, job_id)
        return (job.prices if job and job.prices else {})


def write_stage2(job_id: str, page: int, data: dict) -> None:
    with db.session() as s:
        _get_or_create(s, job_id)
        row = s.get(db.Stage2Result, (job_id, page))
        if row is None:
            row = db.Stage2Result(job_id=job_id, page=page)
            s.add(row)
        row.data = data


def read_stage2(job_id: str, page: int) -> dict | None:
    with db.session() as s:
        row = s.get(db.Stage2Result, (job_id, page))
        return row.data if row else None


# ── Postgres: per-zone rows (individually addressable, soft-deletable) ───────
def replace_zones(job_id: str, page: int, zones: list[dict]) -> list[str]:
    """Replace all zones for a (job, page) with a fresh detection. Returns the
    new zone ids (assigned here if a row omits `id`)."""
    from sqlalchemy import delete as _delete
    ids: list[str] = []
    with db.session() as s:
        _get_or_create(s, job_id)
        s.execute(_delete(db.Zone).where(db.Zone.job_id == job_id, db.Zone.page == page))
        for z in zones:
            zid = z.get("id") or uuid.uuid4().hex[:16]
            ids.append(zid)
            s.add(db.Zone(
                id=zid, job_id=job_id, page=page, code=z["code"], hex=z.get("hex"),
                area_sqft=z.get("area_sqft"), perimeter_lf=z.get("perimeter_lf"),
                geometry=z.get("geometry"), bbox=z.get("bbox"),
                source=z.get("source"), status=z.get("status", "active")))
    return ids


def list_zones(job_id: str, page: int, include_deleted: bool = False) -> list[dict]:
    with db.session() as s:
        q = s.query(db.Zone).filter(db.Zone.job_id == job_id, db.Zone.page == page)
        if not include_deleted:
            q = q.filter(db.Zone.status == "active")
        return [_zone_to_dict(z) for z in q.order_by(db.Zone.area_sqft.desc().nullslast())]


def active_zones(job_id: str, page: int) -> list[dict]:
    return list_zones(job_id, page, include_deleted=False)


def get_zone(zone_id: str) -> dict | None:
    with db.session() as s:
        z = s.get(db.Zone, zone_id)
        return _zone_to_dict(z) if z else None


def set_zone_status(zone_id: str, status: str) -> dict | None:
    """Flip a zone's status (active|deleted). Returns the zone's (job_id, page)
    so the caller can re-render, or None if the id is unknown."""
    with db.session() as s:
        z = s.get(db.Zone, zone_id)
        if z is None:
            return None
        z.status = status
        return {"job_id": z.job_id, "page": z.page, "code": z.code}


def set_zones_status_by_code(job_id: str, page: int, code: str, status: str) -> list[str]:
    """Flip every active/deleted zone of one code to `status`. Returns the ids
    affected (so the caller can record them for undo)."""
    opposite = "deleted" if status == "active" else "active"
    with db.session() as s:
        rows = (s.query(db.Zone)
                .filter(db.Zone.job_id == job_id, db.Zone.page == page,
                        db.Zone.code == code, db.Zone.status == opposite)
                .all())
        ids = []
        for z in rows:
            z.status = status
            ids.append(z.id)
        return ids


def _zone_to_dict(z: "db.Zone") -> dict:
    return {"id": z.id, "job_id": z.job_id, "page": z.page, "code": z.code,
            "hex": z.hex, "area_sqft": z.area_sqft, "perimeter_lf": z.perimeter_lf,
            "geometry": z.geometry, "bbox": z.bbox, "source": z.source,
            "status": z.status}


def sheet_cfg_for_page(cfg: dict, page_index: int) -> dict | None:
    """Build the per-sheet qto_engine config for a 0-based page, merging the
    top-level config (tag pattern, clip %, phase params) with the matching
    sheet entry (by its 1-based 'page'). Returns None if no sheet maps here."""
    for sheet_id, info in (cfg.get("sheets") or {}).items():
        if "page" in info and int(info["page"]) - 1 == page_index:
            return {
                "sheet_id": sheet_id,
                "title": info.get("title", sheet_id),
                "scale_in_per_ft": float(info["scale_in_per_ft"]),
                "tag_pattern": cfg.get("tag_pattern", r"^\(?M[-.]?(\d{1,2})\)?$"),
                "tag_numeric_only": cfg.get("tag_numeric_only", True),
                "clip": {
                    "top": info.get("plan_clip_top_pct", cfg.get("plan_clip_top_pct", 0.05)),
                    "bottom": info.get("plan_clip_bottom_pct", cfg.get("plan_clip_bottom_pct", 0.92)),
                    "left": info.get("plan_clip_left_pct", cfg.get("plan_clip_left_pct", 0.0)),
                    "right": info.get("plan_clip_right_pct", cfg.get("plan_clip_right_pct", 0.80)),
                },
                "phase1_min_zone_sf": info.get("phase1_min_zone_sf", 0),
                "phase2_radius_ft": info.get("phase2_radius_ft", 24),
                "zone_detection_codes": info.get("zone_detection_codes"),
                "phase1_max_zone_sf": info.get("phase1_max_zone_sf", 0),
                "phase2_skip_codes": info.get("phase2_skip_codes"),
                "phase2_force_expand_codes": info.get("phase2_force_expand_codes"),
                "tag_position_overrides": info.get("tag_position_overrides"),
                "hatch_cc_codes": info.get("hatch_cc_codes"),
                "hatch_detect_codes": info.get("hatch_detect_codes"),
                "gray_fill_diff": info.get("gray_fill_diff", 20),
                "hatch_min_zone_sf": info.get("hatch_min_zone_sf", 50.0),
                "hatch_max_zone_sf": info.get("hatch_max_zone_sf"),
                "hatch_zone_search_in": info.get("hatch_zone_search_in", 3.0),
                "hatch_cc_k": info.get("hatch_cc_k", 8),
            }
    return None
