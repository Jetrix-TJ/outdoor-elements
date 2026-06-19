# Stage 1 — Upload Verification

Date: 2026-06-17
Verified by: Playwright end-to-end tests against the running app
(FastAPI backend on :8000 + Vite frontend on :5173).

## Result: ✅ UPLOAD IS WORKING

Both automated tests pass against the live app:

```
Running 2 tests using 1 worker
  ok 1 tests\upload.spec.js:8:1 › upload selects the 6 required landscape pages (34.2s)
  ok 2 tests\upload.spec.js:41:1 › rejects a non-PDF upload (428ms)
  2 passed (35.6s)
```

Screenshot of the working result: `stage1-working.png`.

## What the tests prove

**Test 1 — happy path** (`2811 Kirby - LANDSCAPE.pdf`, 35 pages):
- Drag-drop dropzone renders.
- Uploading the PDF triggers Stage 1.
- The summary shows **"6 required"**.
- All six expected sheet cards appear: **L1.01, L1.02, L1.04, L5.01, L5.02, L5.03**.
- At least one page thumbnail actually decodes (naturalWidth > 0).
- The **Continue → Stage 2** button is enabled.

**Test 2 — validation**: uploading a non-PDF surfaces an error mentioning "pdf".

## Bugs found and fixed during verification

The Playwright tests were written first, then run; they caught three real issues:

1. **Blocking upload (72 s).** In eager mode the Celery task ran synchronously
   inside `POST /api/upload`, so the request never returned in time and the UI
   was stuck on "Working…". → Fixed: upload now dispatches the job to a FastAPI
   **BackgroundTask** (eager) or a Celery worker (Redis) and returns in ~0.7 s.

2. **Slow selection (72 s → 27 s).** `get_drawings()` was parsing every page,
   including the huge L4 detail sheets. → Fixed: skip color counting on pages the
   title filter already drops, and use `get_cdrawings()` (C-level).

3. **Status race ("Could not load job status").** The background task rewrote
   `status.json` while a poll read it half-written → 500. → Fixed: **atomic
   writes** (`os.replace`) plus a tolerant re-read.

## How to reproduce

```bash
# terminal 1 — backend (from outdoor-elements/)
.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000
# terminal 2 — frontend (from outdoor-elements/frontend/)
npm run dev
# terminal 3 — tests (from outdoor-elements/frontend/)
npx playwright test
```
