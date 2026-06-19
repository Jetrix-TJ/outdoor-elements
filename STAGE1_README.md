# Stage 1 — Upload & Required-Page Selection

Upload a drawing PDF; a Celery worker keeps only the takeoff plan sheets and
drops covers, notes, grading, lighting, details, sections and elevations — the
same triage a human estimator does first. **Deterministic, no AI** (the input is
vector CAD).

See the design + the two-filter rule in
`docs/superpowers/specs/2026-06-17-stage1-page-selection-design.md`.

## Architecture

```
React/Vite (5173) ──/api proxy──► FastAPI (8000) ──► Celery task stage1_select
                                                       (Redis broker, or inline
                                                        "eager" mode if no Redis)
```

- `backend/selection.py` — the two-filter page selector (the engine).
- `backend/tasks.py` — `stage1_select` Celery task (classify + render thumbs).
- `backend/celery_app.py` — Redis broker; auto-falls back to eager if Redis down.
- `backend/main.py` — FastAPI: `/api/upload`, `/api/jobs/{id}`, thumbnails.
- `frontend/` — upload UI + kept-pages grid + Continue.

## Run it

**1. Backend** (from `outdoor-elements/`):

```bash
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000
```

Without Redis it runs in **eager mode** — upload is processed inline, fully
working. The UI shows "ran inline (no Redis)".

**2. Frontend** (from `outdoor-elements/frontend/`):

```bash
npm install
npm run dev      # http://localhost:5173
```

**3. (Optional) Real Celery workers** — start Redis, then:

```bash
celery -A backend.celery_app worker --loglevel=info --pool=solo
```

The same task code now runs on the worker; the UI shows "ran on Celery worker".
On Windows, run Redis via Docker (`docker run -p 6379:6379 redis`), Memurai, or WSL.

## Validated

On `2811 Kirby - LANDSCAPE.pdf` (35 pages) Stage 1 keeps exactly the human's set:
**L1.01, L1.02, L1.04, L5.01, L5.02, L5.03** — 6/6, no false positives.

The pool set (`AQ` sheets) is monochrome with no color legend and a different
title block; those pages are reported as **pool-style** for a future pool mode.

## Next

- Stage 2: vector line extraction on the kept pages.
- Stage 3: square-footage measurement from the lines.
