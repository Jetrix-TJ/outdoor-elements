// Thin API client for the Stage 1 backend. Calls go through the Vite proxy.

export async function uploadPdf(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  if (!res.ok) throw new Error((await res.json()).detail || "Upload failed");
  return res.json(); // { job_id, filename, eager }
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

export async function pollStage2(jobId, page, onUpdate, intervalMs = 800) {
  while (true) {
    const s = await getStage2(jobId, page);
    onUpdate(s);
    if (s.status === "done" || s.status === "error") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
