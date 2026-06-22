// Decide where to land when resuming a previously-processed job, from the
// Stage-2 status map returned by GET /api/jobs/{id}/stage2/status.
//
// pages: { "<keptIndex>": "pending"|"queued"|"running"|"done"|"error" }  (kept pages only)
// returns: { view: "stage1"|"stage2"|"stage3", firstDone: number|null }
//
// Rule: ANY extracted sheet -> Stage 3 (the estimate aggregates whatever is
// done, so a partially-extracted job still resumes on its estimate). Only an
// untouched job (nothing extracted) starts at Stage 1. firstDone is the lowest
// "done" page index (or null) — used so "← Back" from Stage 3 lands on a real sheet.
export function pickResumeStage(pages) {
  const entries = Object.entries(pages || {});
  const doneIdx = entries
    .filter(([, s]) => s === "done")
    .map(([k]) => Number(k));
  const firstDone = doneIdx.length ? Math.min(...doneIdx) : null;
  if (doneIdx.length) return { view: "stage3", firstDone };
  return { view: "stage1", firstDone: null };
}
