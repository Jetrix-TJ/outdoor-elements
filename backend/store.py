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
                "hatch_min_zone_sf": info.get("hatch_min_zone_sf", 50.0),
                "hatch_max_zone_sf": info.get("hatch_max_zone_sf"),
                "hatch_zone_search_in": info.get("hatch_zone_search_in", 3.0),
                "hatch_cc_k": info.get("hatch_cc_k", 8),
            }
    return None
