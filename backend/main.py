"""FastAPI app — Stage 1: upload a drawing PDF, select the required pages.

    uvicorn backend.main:app --reload --port 8000

Endpoints:
    POST /api/upload                multipart 'file' -> {job_id}
    GET  /api/jobs/{job_id}         job status + per-page classification
    GET  /api/jobs/{job_id}/thumbs/{name}   page thumbnail PNG
"""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pydantic import BaseModel

from . import db, pricing, store
from .celery_app import EAGER
from .schemas import JobStatus, UploadResponse
from .tasks import (run_stage1, run_stage1_config, run_stage2, stage1_config,
                    stage1_select, stage2_detect)

app = FastAPI(title="Outdoor Elements — Takeoff (Stage 1)")

# Vite dev server runs on 5173; allow it during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _init_db() -> None:
    """Create the Postgres tables if missing (fail-fast, but don't crash if the
    DB is briefly unavailable — the lazy session() will retry)."""
    try:
        db.init_db()
        print("DB ready:", db.DATABASE_URL.rsplit("@", 1)[-1])
    except Exception as exc:  # noqa: BLE001
        print(f"DB init deferred ({type(exc).__name__}): start Postgres, see docker-compose.yml")


@app.get("/api/health")
def health() -> dict:
    db_ok = True
    try:
        with db.session():
            pass
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"ok": True, "eager": EAGER, "db": db_ok}


@app.post("/api/upload", response_model=UploadResponse)
async def upload(background: BackgroundTasks, file: UploadFile = File(...)) -> UploadResponse:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")

    job_id = store.new_job_id()
    store.pdf_path(job_id).write_bytes(await file.read())
    store.write_status(job_id, {"job_id": job_id, "filename": file.filename, "status": "queued"})

    # Dispatch Stage 1 (page selection) + Gemini auto-config WITHOUT blocking.
    # No Redis -> background threads; Redis up -> Celery workers.
    if EAGER:
        background.add_task(run_stage1, job_id, file.filename)
        background.add_task(run_stage1_config, job_id, file.filename)
    else:
        stage1_select.delay(job_id, file.filename)
        stage1_config.delay(job_id, file.filename)
    return UploadResponse(job_id=job_id, filename=file.filename, eager=EAGER)


class ScaleEdit(BaseModel):
    sheet_id: str
    scale_in_per_ft: float


@app.get("/api/jobs/{job_id}/config")
def get_config(job_id: str) -> dict:
    cfg = store.read_config(job_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not ready.")
    return cfg


@app.patch("/api/jobs/{job_id}/config/scale")
def edit_scale(job_id: str, edit: ScaleEdit) -> dict:
    """Correct a sheet's scale (the reviewable-config step)."""
    cfg = store.read_config(job_id)
    if cfg is None or edit.sheet_id not in cfg.get("sheets", {}):
        raise HTTPException(status_code=404, detail="Unknown job or sheet.")
    cfg["sheets"][edit.sheet_id]["scale_in_per_ft"] = float(edit.scale_in_per_ft)
    store.write_config(job_id, cfg)
    return cfg


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str) -> JobStatus:
    data = store.read_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return JobStatus(**data)


@app.get("/api/jobs/{job_id}/thumbs/{name}")
def thumb(job_id: str, name: str) -> FileResponse:
    path = store.thumbs_dir(job_id) / name
    if not path.exists() or ".." in name:
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/jobs/{job_id}/page/{index}/preview")
def page_preview(job_id: str, index: int) -> FileResponse:
    """High-resolution render of one page, on demand (cached). Used by the
    click-to-enlarge preview in the UI."""
    pdf = store.pdf_path(job_id)
    if not pdf.exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    out = store.previews_dir(job_id) / f"p{index}.png"
    if not out.exists():
        from . import selection
        selection.render_thumb(pdf, index, out, dpi=130)
    return FileResponse(out, media_type="image/png")


# ---------- Stage 2: detect & color surface regions on a page ----------
@app.post("/api/jobs/{job_id}/stage2/{page}")
def start_stage2(job_id: str, page: int, background: BackgroundTasks) -> dict:
    if not store.pdf_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    store.write_stage2(job_id, page, {"job_id": job_id, "page": page, "status": "queued"})
    if EAGER:
        background.add_task(run_stage2, job_id, page)
    else:
        stage2_detect.delay(job_id, page)
    return {"job_id": job_id, "page": page, "eager": EAGER}


@app.get("/api/jobs/{job_id}/stage2/{page}")
def stage2_status(job_id: str, page: int) -> dict:
    data = store.read_stage2(job_id, page)
    if data is None:
        raise HTTPException(status_code=404, detail="Stage 2 not started for this page.")
    return data


@app.get("/api/jobs/{job_id}/stage2/{page}/overlay")
def stage2_overlay(job_id: str, page: int) -> FileResponse:
    path = store.stage2_dir(job_id) / f"overlay_p{page}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Overlay not ready.")
    return FileResponse(path, media_type="image/png")


# ---------- Manual correction: remove a shaded zone by click ----------
class RemovePoint(BaseModel):
    x: float  # fractional 0..1 of the overlay width
    y: float  # fractional 0..1 of the overlay height


@app.post("/api/jobs/{job_id}/stage2/{page}/pick")
def pick_zone(job_id: str, page: int, pt: RemovePoint) -> dict:
    """Identify the zone at a click without removing it (select + confirm)."""
    from .tasks import pick_region
    return pick_region(job_id, page, pt.x, pt.y)


@app.post("/api/jobs/{job_id}/stage2/{page}/remove")
def remove_zone(job_id: str, page: int, pt: RemovePoint) -> dict:
    if not store.pdf_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    from .tasks import remove_region
    return remove_region(job_id, page, pt.x, pt.y)


class RemoveBatch(BaseModel):
    points: list[RemovePoint]


@app.post("/api/jobs/{job_id}/stage2/{page}/remove_batch")
def remove_batch_zones(job_id: str, page: int, batch: RemoveBatch) -> dict:
    from .tasks import remove_batch
    return remove_batch(job_id, page, [{"x": p.x, "y": p.y} for p in batch.points])


class MaterialRef(BaseModel):
    code: str


@app.post("/api/jobs/{job_id}/stage2/{page}/remove_material")
def remove_material_zones(job_id: str, page: int, ref: MaterialRef) -> dict:
    from .tasks import remove_material
    return remove_material(job_id, page, ref.code)


@app.post("/api/jobs/{job_id}/stage2/{page}/undo")
def undo_edit(job_id: str, page: int) -> dict:
    from .tasks import undo_last
    return undo_last(job_id, page)


# ---------- Per-zone addressing: list / delete-by-id / restore ----------
@app.get("/api/jobs/{job_id}/stage2/{page}/zones")
def list_zones(job_id: str, page: int, include_deleted: bool = False) -> dict:
    """All zones for a page, each with its stable id (for select + delete)."""
    return {"zones": store.list_zones(job_id, page, include_deleted=include_deleted)}


@app.get("/api/jobs/{job_id}/zones/{zone_id}")
def get_zone(job_id: str, zone_id: str) -> dict:
    z = store.get_zone(zone_id)
    if z is None or z["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="Unknown zone id.")
    return z


@app.delete("/api/jobs/{job_id}/zones/{zone_id}")
def delete_zone_by_id(job_id: str, zone_id: str) -> dict:
    z = store.get_zone(zone_id)
    if z is None or z["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="Unknown zone id.")
    from .tasks import delete_zone
    return delete_zone(job_id, zone_id)


@app.post("/api/jobs/{job_id}/zones/{zone_id}/restore")
def restore_zone_by_id(job_id: str, zone_id: str) -> dict:
    z = store.get_zone(zone_id)
    if z is None or z["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="Unknown zone id.")
    from .tasks import restore_zone
    return restore_zone(job_id, zone_id)


# ---------- Pricing: quantities × unit rates → costed estimate ----------
class RateEdit(BaseModel):
    code: str
    rate: float


def _pricing_for(job_id: str, page: int) -> dict:
    s2 = store.read_stage2(job_id, page)
    if not s2 or not s2.get("groups"):
        raise HTTPException(status_code=404, detail="Run Stage 2 for this page first.")
    areas = {g["label"]: g["sqft"] for g in s2["groups"] if g.get("label")}
    rates = store.read_prices(job_id)
    cfg = store.read_config(job_id) or {}
    mats = cfg.get("materials") if isinstance(cfg.get("materials"), dict) else {}
    names: dict = {}
    changed = False
    for code in areas:
        info = (mats or {}).get(code)
        nm = info.get("name") if isinstance(info, dict) else (info if isinstance(info, str) else "")
        names[code] = nm or ""
        if code not in rates:
            rates[code] = pricing.default_rate(code, names[code])
            changed = True
    if changed:
        store.write_prices(job_id, rates)
    return pricing.price_takeoff(areas, rates, names)


@app.get("/api/jobs/{job_id}/pricing")
def get_pricing(job_id: str, page: int) -> dict:
    return _pricing_for(job_id, page)


@app.patch("/api/jobs/{job_id}/pricing")
def edit_rate(job_id: str, edit: RateEdit, page: int) -> dict:
    rates = store.read_prices(job_id)
    rates[edit.code] = float(edit.rate)
    store.write_prices(job_id, rates)
    return _pricing_for(job_id, page)
