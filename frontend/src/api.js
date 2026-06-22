// Thin API client for the Stage 1 backend. Calls go through the Vite proxy.

// Validate the access passcode against the backend (server-side check).
export async function login(passcode) {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ passcode }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Incorrect passcode");
  return res.json();
}

// Cloud Run has a 32 MB request body limit; large PDFs go direct to GCS.
const DIRECT_UPLOAD_THRESHOLD = 28 * 1024 * 1024; // 28 MB

async function _jsonOrText(res) {
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json().catch(() => ({})) : { detail: await res.text() };
}

// Direct-to-GCS resumable upload for a single large file (prod path). Throws if
// GCS isn't reachable (e.g. local dev with no bucket/credentials).
async function _directGcsUpload(file) {
  const urlRes = await fetch("/api/upload-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size }),
  });
  if (!urlRes.ok) {
    const body = await _jsonOrText(urlRes);
    throw new Error(body.detail || "Could not get upload URL");
  }
  const { job_id, upload_url } = await urlRes.json();

  // PUT the file directly to GCS (no Cloud Run hop). GCS omits
  // Access-Control-Allow-Origin on the 200 PUT, so the browser CORS check throws
  // TypeError even though the file landed — swallow it; /start verifies existence.
  try {
    const putRes = await fetch(upload_url, {
      method: "PUT",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
    if (!putRes.ok) throw new Error(`GCS upload failed (${putRes.status})`);
  } catch (err) {
    if (!(err instanceof TypeError)) throw err;
  }

  const startRes = await fetch(
    `/api/jobs/${job_id}/start?filename=${encodeURIComponent(file.name)}`,
    { method: "POST" }
  );
  if (!startRes.ok) {
    const body = await _jsonOrText(startRes);
    throw new Error(body.detail || "Could not start processing");
  }
  return startRes.json();
}

export async function uploadPdf(files) {
  // accepts a single File or a FileList/array — merged into one set server-side
  const list = files && files.length !== undefined ? Array.from(files) : [files];

  // Single large file → try direct-to-GCS (bypasses Cloud Run's 32 MB limit in
  // prod). If GCS isn't configured (local dev), fall back to the multipart path
  // below — uvicorn has no body-size limit, so large files upload fine locally.
  if (list.length === 1 && list[0].size > DIRECT_UPLOAD_THRESHOLD) {
    try {
      return await _directGcsUpload(list[0]);
    } catch (err) {
      console.warn("Direct GCS upload unavailable — falling back to multipart:", err.message);
    }
  }

  // Small files, multiple files, or GCS fallback — standard multipart upload.
  const form = new FormData();
  for (const f of list) form.append("files", f);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  if (!res.ok) {
    const body = await _jsonOrText(res);
    throw new Error(body.detail || "Upload failed");
  }
  return res.json();
}

export async function getJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) throw new Error("Could not load job status");
  return res.json();
}

export function thumbUrl(jobId, thumb) {
  // thumb is like "thumbs/p2.png"
  return `/api/jobs/${jobId}/${thumb}`;
}

export function previewUrl(jobId, index) {
  // high-res on-demand render of one page
  return `/api/jobs/${jobId}/page/${index}/preview`;
}

// Poll a job until it reaches a terminal state.
export async function pollJob(jobId, onUpdate, intervalMs = 700) {
  while (true) {
    const job = await getJob(jobId);
    onUpdate(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// ---------- Stage 1: Gemini auto-config (reviewable) ----------
export async function getConfig(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/config`);
  if (!res.ok) return null; // 404 until ready
  return res.json();
}

export async function editScale(jobId, sheetId, scaleInPerFt) {
  const res = await fetch(`/api/jobs/${jobId}/config/scale`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheet_id: sheetId, scale_in_per_ft: scaleInPerFt }),
  });
  if (!res.ok) throw new Error("Could not update scale");
  return res.json();
}

// ---------- Stage 2: detect & color surface regions ----------
export async function startStage2(jobId, page) {
  const res = await fetch(`/api/jobs/${jobId}/stage2/${page}`, { method: "POST" });
  if (!res.ok) throw new Error("Could not start Stage 2");
  return res.json();
}

export async function getStage2(jobId, page) {
  const res = await fetch(`/api/jobs/${jobId}/stage2/${page}`);
  if (!res.ok) throw new Error("Could not load Stage 2 status");
  return res.json();
}

// Kick off detection of ALL kept pages (batch, background).
export async function startAllStage2(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/stage2/all`, { method: "POST" });
  if (!res.ok) throw new Error("Could not start batch extraction");
  return res.json();
}

// Status of every kept page: { pages: { "<index>": "pending|queued|running|done|error" } }
export async function getStage2Status(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/stage2/status`);
  if (!res.ok) throw new Error("Could not load Stage 2 status");
  return res.json();
}

export function stage2OverlayUrl(jobId, page) {
  return `/api/jobs/${jobId}/stage2/${page}/overlay`;
}

// Manual correction.
async function _post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Request failed");
  return res.json();
}

// Identify the zone at a click (select + confirm), without removing it.
export const pickZone = (jobId, page, x, y) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/pick`, { x, y });

// Remove the single zone at fractional coords (0..1).
export const removeZone = (jobId, page, x, y) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/remove`, { x, y });

// Remove every zone hit by a list of clicks (multi-select delete).
export const removeBatch = (jobId, page, points) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/remove_batch`, { points });

// Remove every zone of one material.
export const removeMaterial = (jobId, page, code) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/remove_material`, { code });

// Undo the last removal (one level).
export const undoEdit = (jobId, page) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/undo`);

// ---------- Per-zone: list / delete-by-id / batch / restore ----------
// Returns { zones, page:{width,height} } — page size drives the SVG overlay viewBox.
export async function listZones(jobId, page, includeDeleted = false) {
  const res = await fetch(
    `/api/jobs/${jobId}/stage2/${page}/zones?include_deleted=${includeDeleted}`);
  if (!res.ok) throw new Error("Could not load zones");
  return res.json();
}

// Soft-delete several zones by id in one request (marquee / multi-select).
export const deleteZonesBatch = (jobId, page, ids) =>
  _post(`/api/jobs/${jobId}/stage2/${page}/zones/delete_batch`, { ids });

export async function deleteZone(jobId, zoneId) {
  const res = await fetch(`/api/jobs/${jobId}/zones/${zoneId}`, { method: "DELETE" });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Delete failed");
  return res.json();
}

export const restoreZone = (jobId, zoneId) =>
  _post(`/api/jobs/${jobId}/zones/${zoneId}/restore`);

// Pricing: quantities × unit rates.
export async function getPricing(jobId, page) {
  const res = await fetch(`/api/jobs/${jobId}/pricing?page=${page}`);
  if (!res.ok) throw new Error("Could not load pricing");
  return res.json();
}

export async function editRate(jobId, page, code, rate) {
  const res = await fetch(`/api/jobs/${jobId}/pricing?page=${page}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, rate }),
  });
  if (!res.ok) throw new Error("Could not update rate");
  return res.json();
}

// Combined project estimate (all detected pages) in OE scope-of-work format.
export async function getEstimate(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/estimate`);
  if (!res.ok) throw new Error("Could not load estimate");
  return res.json();
}

export async function pollStage2(jobId, page, onUpdate, intervalMs = 800) {
  while (true) {
    const s = await getStage2(jobId, page);
    onUpdate(s);
    if (s.status === "done" || s.status === "error") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// ---------- Pool scope: line-items from the estimate PDF ----------
export async function getPoolScope(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/pool-scope`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to load pool scope");
  return res.json();
}

// ---------- Previous jobs ----------
export async function listJobs() {
  const res = await fetch("/api/jobs");
  if (!res.ok) throw new Error("Failed to load jobs");
  return res.json();
}

export async function deleteJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete job");
  return res.json();
}
