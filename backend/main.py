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

import hashlib
import os
import secrets

from . import db, estimate_pricing, pricing, store
from .celery_app import EAGER
from .schemas import JobStatus, UploadResponse
from .tasks import (run_stage1, run_stage1_config, run_stage2, stage1_config,
                    stage1_select, stage2_detect, detect_kept_pages, stage2_detect_all)

app = FastAPI(title="Outdoor Elements — Takeoff (Stage 1)")

# In production (Cloud Run) we serve the SPA from the same origin so no CORS is
# needed. In dev the Vite proxy handles /api, but localhost:5173 is listed here
# so that direct API testing still works. ALLOWED_ORIGINS env var overrides both.
_cors_origins = (
    os.environ["ALLOWED_ORIGINS"].split(",")
    if os.environ.get("ALLOWED_ORIGINS")
    else ["http://localhost:5173", "http://127.0.0.1:5173"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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


# ---------- Passcode gate ----------
# Single shared passcode, validated server-side (never shipped in the JS bundle).
# Override the default with the OE_PASSCODE environment variable.
def _passcode() -> str:
    return os.environ.get("OE_PASSCODE", "2811")


class LoginReq(BaseModel):
    passcode: str


@app.post("/api/login")
def login(req: LoginReq) -> dict:
    """Validate the access passcode. Constant-time compare to avoid timing leaks."""
    if secrets.compare_digest(req.passcode.strip(), _passcode()):
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Incorrect passcode.")


def _merge_pdfs(blobs: list[bytes]) -> bytes:
    """Concatenate several PDFs into one set (one job covers all the files)."""
    import fitz
    out = fitz.open()
    for b in blobs:
        src = fitz.open(stream=b, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes()
    out.close()
    return data


@app.post("/api/upload", response_model=UploadResponse)
async def upload(background: BackgroundTasks,
                 files: list[UploadFile] = File(...)) -> UploadResponse:
    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(status_code=400, detail="Please upload .pdf file(s).")

    blobs = [await f.read() for f in pdfs]
    # Multiple files = parts of one drawing set -> merge into a single set so page
    # selection + extraction cover all of them.
    data = blobs[0] if len(blobs) == 1 else _merge_pdfs(blobs)
    filename = (pdfs[0].filename if len(pdfs) == 1
                else f"{len(pdfs)} files ({pdfs[0].filename} +{len(pdfs) - 1})")
    content_hash = hashlib.sha256(data).hexdigest()

    # Same set uploaded before? Resume that job so its saved edits/deletions and
    # already-computed pages/zones come back instead of starting from scratch.
    existing = store.find_job_by_hash(content_hash)
    if existing and store.pdf_path(existing).exists():
        st = store.read_status(existing) or {}
        return UploadResponse(job_id=existing, eager=EAGER, resumed=True,
                              filename=st.get("filename") or filename)

    job_id = store.new_job_id()
    store.pdf_path(job_id).write_bytes(data)
    store.write_status(job_id, {"job_id": job_id, "filename": filename, "status": "queued"})
    store.set_content_hash(job_id, content_hash)

    # Dispatch Stage 1 (page selection) + Gemini auto-config WITHOUT blocking.
    # No Redis -> background threads; Redis up -> Celery workers.
    if EAGER:
        background.add_task(run_stage1, job_id, filename)
        background.add_task(run_stage1_config, job_id, filename)
    else:
        stage1_select.delay(job_id, filename)
        stage1_config.delay(job_id, filename)
    return UploadResponse(job_id=job_id, filename=filename, eager=EAGER)


class ScaleEdit(BaseModel):
    sheet_id: str
    scale_in_per_ft: float


@app.get("/api/jobs/{job_id}/config")
def get_config(job_id: str) -> dict:
    cfg = store.read_config(job_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not ready.")
    return cfg


@app.get("/api/jobs/{job_id}/pool-scope")
def get_pool_scope(job_id: str) -> dict:
    cfg = store.read_config(job_id)
    if cfg is None or "pool_scope" not in cfg:
        raise HTTPException(status_code=404, detail="Pool scope not available.")
    return cfg["pool_scope"]


@app.patch("/api/jobs/{job_id}/config/scale")
def edit_scale(job_id: str, edit: ScaleEdit) -> dict:
    """Correct a sheet's scale (the reviewable-config step)."""
    cfg = store.read_config(job_id)
    if cfg is None or edit.sheet_id not in cfg.get("sheets", {}):
        raise HTTPException(status_code=404, detail="Unknown job or sheet.")
    cfg["sheets"][edit.sheet_id]["scale_in_per_ft"] = float(edit.scale_in_per_ft)
    store.write_config(job_id, cfg)
    return cfg


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    """Return all jobs ordered by most-recent first, for the Previous Jobs panel."""
    with db.session() as s:
        jobs = s.query(db.Job).order_by(db.Job.created_at.desc()).all()
        result = []
        for j in jobs:
            st = j.status or {}
            result.append({
                "job_id": j.job_id,
                "filename": j.filename,
                "status": st.get("status"),
                "page_count": st.get("page_count"),
                "kept_count": st.get("kept_count"),
                "created_at": j.created_at.isoformat() if j.created_at else None,
            })
        return result


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    """Delete a job: remove DB rows (job + stage2 results + zones) and filesystem files."""
    import shutil
    with db.session() as s:
        job = s.get(db.Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id.")
        s.query(db.Zone).filter(db.Zone.job_id == job_id).delete()
        s.query(db.Stage2Result).filter(db.Stage2Result.job_id == job_id).delete()
        s.delete(job)
    job_path = store.job_dir(job_id)
    if job_path.exists():
        shutil.rmtree(job_path)
    return {"deleted": job_id}


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str) -> JobStatus:
    data = store.read_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return JobStatus(**data)


class KeepEdit(BaseModel):
    keep: bool


@app.patch("/api/jobs/{job_id}/pages/{index}/keep")
def set_page_keep(job_id: str, index: int, edit: KeepEdit) -> JobStatus:
    """Manually include/exclude a page from the takeoff set (the reviewable
    page-selection step). Lets the estimator land on exactly the right sheets
    when the auto-classifier keeps too many or misses one."""
    from . import selection
    data = store.read_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    pages = data.get("pages", [])
    target = next((p for p in pages if int(p.get("index", -1)) == index), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown page index.")
    target["keep"] = bool(edit.keep)
    target["pool_style"] = False  # a manual pick is no longer "pool-style/unsorted"
    # Render a thumbnail for a newly-included page so the kept grid shows it.
    if target["keep"] and not target.get("thumb"):
        out = store.thumbs_dir(job_id) / f"p{index}.png"
        try:
            selection.render_thumb(str(store.pdf_path(job_id)), index, out)
            target["thumb"] = f"thumbs/p{index}.png"
        except Exception:  # noqa: BLE001 — thumb is best-effort
            pass
    data["kept_count"] = sum(1 for p in pages if p.get("keep"))
    store.write_status(job_id, data)
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
# NOTE: the literal "/stage2/all" route MUST be declared before the
# "/stage2/{page}" route — FastAPI matches in declaration order, and otherwise
# "all" is captured by {page} and fails int parsing (422).
@app.post("/api/jobs/{job_id}/stage2/all")
def start_stage2_all(job_id: str, background: BackgroundTasks) -> dict:
    """Kick off detection of EVERY kept page (sequential, background)."""
    if not store.pdf_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    status = store.read_status(job_id) or {}
    kept = [p for p in status.get("pages", []) if p.get("keep")]
    # mark not-yet-started kept pages as queued so the UI shows them pending now
    for p in kept:
        if store.read_stage2(job_id, int(p["index"])) is None:
            store.write_stage2(job_id, int(p["index"]),
                               {"job_id": job_id, "page": int(p["index"]), "status": "queued"})
    if EAGER:
        background.add_task(detect_kept_pages, job_id)
    else:
        stage2_detect_all.delay(job_id)
    return {"ok": True, "pages": len(kept)}


@app.post("/api/jobs/{job_id}/stage2/{page}")
def start_stage2(job_id: str, page: int, background: BackgroundTasks,
                 force: bool = False) -> dict:
    if not store.pdf_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    # Resume-safe: if this page was already detected (and possibly edited), do NOT
    # re-detect — that would wipe the user's zone deletions. Return the saved
    # result (the frontend polls GET). Pass ?force=true to deliberately re-detect.
    existing = store.read_stage2(job_id, page)
    if not force and existing and existing.get("status") == "done":
        return {"job_id": job_id, "page": page, "eager": EAGER, "cached": True}
    store.write_stage2(job_id, page, {"job_id": job_id, "page": page, "status": "queued"})
    if EAGER:
        background.add_task(run_stage2, job_id, page, force)
    else:
        stage2_detect.delay(job_id, page, force)
    return {"job_id": job_id, "page": page, "eager": EAGER}


@app.get("/api/jobs/{job_id}/stage2/status")
def stage2_status_all(job_id: str) -> dict:
    """Status of every kept page: pending|queued|running|done|error."""
    status = store.read_status(job_id) or {}
    pages: dict[str, str] = {}
    for p in status.get("pages", []):
        if not p.get("keep"):
            continue
        s2 = store.read_stage2(job_id, int(p["index"]))
        pages[str(int(p["index"]))] = (s2 or {}).get("status", "pending")
    return {"pages": pages}


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


@app.get("/api/jobs/{job_id}/stage2/{page}/qto")
def stage2_qto(job_id: str, page: int) -> FileResponse:
    """Render a human-style QTO output: the colored plan + a takeoff legend."""
    if store.read_stage2(job_id, page) is None:
        raise HTTPException(status_code=404, detail="Run Stage 2 first.")
    from . import qto_output
    path = qto_output.render_qto(job_id, page)
    return FileResponse(path, media_type="image/png",
                        filename=f"QTO_{job_id}_p{page}.png")


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


# ---------- Per-zone addressing: list / delete-by-id / batch / restore ----------
def _page_size(job_id: str, page: int) -> dict | None:
    """Base-page size in PDF points (for the interactive SVG overlay viewBox)."""
    pdf = store.pdf_path(job_id)
    if not pdf.exists():
        return None
    import fitz
    doc = fitz.open(pdf)
    try:
        r = doc[page].rect
        return {"width": r.width, "height": r.height}
    finally:
        doc.close()


@app.get("/api/jobs/{job_id}/stage2/{page}/zones")
def list_zones(job_id: str, page: int, include_deleted: bool = False) -> dict:
    """All zones for a page (each with its stable id + polygon geometry) plus the
    base-page size, so the frontend can draw an interactive SVG overlay."""
    return {"zones": store.list_zones(job_id, page, include_deleted=include_deleted),
            "page": _page_size(job_id, page)}


class ZoneIds(BaseModel):
    ids: list[str]


@app.post("/api/jobs/{job_id}/stage2/{page}/zones/delete_batch")
def delete_zones_batch(job_id: str, page: int, body: ZoneIds) -> dict:
    """Soft-delete several zones by id in one pass (one re-render). For the
    marquee / multi-select delete in the editor."""
    from .tasks import delete_zones
    return delete_zones(job_id, page, body.ids)


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


def _estimate_src(job_id: str) -> str | None:
    """Path to this job's pricing estimate PDF (per-job upload, else env fallback)."""
    p = store.estimate_path(job_id)
    if p.exists():
        return str(p)
    env = os.environ.get("OE_ESTIMATE_PATH")
    return env if env and os.path.exists(env) else None


def _estimate_rates(job_id: str) -> dict:
    """Per-material unit rates derived from the client's estimate, or {} if none."""
    src = _estimate_src(job_id)
    if not src:
        return {}
    try:
        return estimate_pricing.parse_estimate(src).rate_table()
    except Exception:  # noqa: BLE001
        return {}


def _pricing_for(job_id: str, page: int) -> dict:
    s2 = store.read_stage2(job_id, page)
    if not s2 or not s2.get("groups"):
        raise HTTPException(status_code=404, detail="Run Stage 2 for this page first.")
    areas = {g["label"]: g["sqft"] for g in s2["groups"] if g.get("label")}
    rates = store.read_prices(job_id)
    cfg = store.read_config(job_id) or {}
    mats = cfg.get("materials") if isinstance(cfg.get("materials"), dict) else {}
    est_rates = _estimate_rates(job_id)   # real rates from the client estimate
    names: dict = {}
    changed = False
    for code in areas:
        info = (mats or {}).get(code)
        nm = info.get("name") if isinstance(info, dict) else (info if isinstance(info, str) else "")
        names[code] = nm or ""
        if code not in rates:
            er = est_rates.get(code)
            rates[code] = (er["rate"] if er and er.get("rate")
                           else pricing.default_rate(code, names[code]))
            changed = True
    if changed:
        store.write_prices(job_id, rates)
    return pricing.price_takeoff(areas, rates, names)


@app.get("/api/jobs/{job_id}/pricing")
def get_pricing(job_id: str, page: int) -> dict:
    return _pricing_for(job_id, page)


@app.get("/api/jobs/{job_id}/estimate")
def get_estimate(job_id: str) -> dict:
    """Combined project estimate (all detected pages) in OE scope-of-work format."""
    from . import estimate
    return estimate.build_estimate(job_id)


@app.patch("/api/jobs/{job_id}/pricing")
def edit_rate(job_id: str, edit: RateEdit, page: int) -> dict:
    rates = store.read_prices(job_id)
    rates[edit.code] = float(edit.rate)
    store.write_prices(job_id, rates)
    return _pricing_for(job_id, page)


@app.post("/api/jobs/{job_id}/estimate")
async def upload_estimate(job_id: str, file: UploadFile = File(...)) -> dict:
    """Attach the client's pricing estimate PDF to a job (drives rate derivation)."""
    if not store.pdf_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Unknown job id.")
    data = await file.read()
    store.estimate_path(job_id).write_bytes(data)
    est = estimate_pricing.parse_estimate(str(store.estimate_path(job_id)))
    return {"ok": True, "filename": file.filename, "grand_total": est.grand_total,
            "materials_with_rates": sum(1 for v in est.rate_table().values() if v.get("rate"))}


@app.get("/api/jobs/{job_id}/pricing/compare")
def pricing_compare(job_id: str, page: int) -> dict:
    """Human estimate vs AI: price our measured quantities with the rates derived
    from the client's estimate, line-by-line and per subsection."""
    s2 = store.read_stage2(job_id, page)
    if not s2 or not s2.get("groups"):
        raise HTTPException(status_code=404, detail="Run Stage 2 for this page first.")
    measured = {g["label"]: g["sqft"] for g in s2["groups"] if g.get("label")}
    src = _estimate_src(job_id)
    if not src:
        return {"available": False,
                "message": "No estimate attached — upload the client's pricing PDF to compare."}
    est = estimate_pricing.parse_estimate(src)
    cmp = estimate_pricing.price_ai(measured, est.rate_table())
    return {"available": True, "grand_total": est.grand_total,
            "section_totals": est.section_totals, **cmp}


# ---------- Large-file upload: direct-to-GCS path (bypasses Cloud Run 32 MB limit) ----------
GCS_JOBS_BUCKET = os.environ.get("GCS_JOBS_BUCKET", "outdoor-elements-499605-jobs")


class UploadUrlRequest(BaseModel):
    filename: str
    size: int  # bytes


@app.post("/api/upload-url")
async def get_upload_url(req: UploadUrlRequest) -> dict:
    """For PDFs > 32 MB: returns a GCS resumable-upload session URI for direct browser upload.
    After the PUT completes, call POST /api/jobs/{job_id}/start to begin processing.
    """
    if not req.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")
    try:
        # Create the GCS client/session FIRST — if GCS is unavailable, fail before
        # writing any job status so we don't orphan a "queued" job.
        from google.cloud import storage as gcs
        client = gcs.Client()
        job_id = store.new_job_id()
        blob = client.bucket(GCS_JOBS_BUCKET).blob(f"{job_id}/upload.pdf")
        upload_url = blob.create_resumable_upload_session(
            content_type="application/pdf",
            size=req.size,
        )
        store.write_status(job_id, {"job_id": job_id, "filename": req.filename, "status": "queued"})
        return {"job_id": job_id, "upload_url": upload_url}
    except Exception as exc:  # noqa: BLE001
        # On Cloud Run the 32 MB request-body limit means a large file CANNOT fall
        # back to a multipart upload — surface a clear, actionable error instead of
        # letting the client retry into a 413. Locally there is no such limit, so
        # tell the client it's safe to use the multipart /api/upload path.
        if os.environ.get("K_SERVICE"):  # set only on Cloud Run
            raise HTTPException(status_code=503, detail=(
                f"Large-file upload needs Cloud Storage but it failed: {exc}. "
                f"Verify bucket '{GCS_JOBS_BUCKET}' exists and the Cloud Run service "
                f"account has 'Storage Object Admin' on it."))
        return {"fallback": True}


@app.post("/api/jobs/{job_id}/start")
async def start_job(job_id: str, background: BackgroundTasks, filename: str = "") -> dict:
    """Kick off Stage 1 after a direct-to-GCS upload has completed.

    The browser PUTs the PDF straight into the GCS jobs bucket, which is NOT the
    container's local disk. So before processing we pull the blob down to the
    local job path the pipeline reads from (store.pdf_path). If it's already
    present (local dev, or a FUSE mount), we skip the download."""
    pdf = store.pdf_path(job_id)
    try:
        have_local = pdf.exists()
    except Exception:  # noqa: BLE001 — a mount glitch shouldn't crash the request
        have_local = False

    if not have_local:
        try:
            from google.cloud import storage as gcs
            blob = gcs.Client().bucket(GCS_JOBS_BUCKET).blob(f"{job_id}/upload.pdf")
            if not blob.exists():
                raise HTTPException(status_code=404,
                                    detail="PDF not found — ensure the GCS upload completed.")
            pdf.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(pdf))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"Could not fetch the uploaded PDF from storage: {exc}")

    store.write_status(job_id, {"job_id": job_id, "filename": filename, "status": "queued"})
    if EAGER:
        background.add_task(run_stage1, job_id, filename)
        background.add_task(run_stage1_config, job_id, filename)
    else:
        stage1_select.delay(job_id, filename)
        stage1_config.delay(job_id, filename)
    return UploadResponse(job_id=job_id, filename=filename, eager=EAGER)


# ---------- SPA catch-all (production: Cloud Run serves both API + frontend) ----------
# Must come LAST so all /api/* routes above take precedence.
from pathlib import Path as _Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_DIST = _Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str):
        from fastapi.responses import FileResponse as _FR
        return _FR(str(_DIST / "index.html"))
