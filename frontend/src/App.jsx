import { useEffect, useRef, useState } from "react";
import {
  uploadPdf, pollJob, thumbUrl, previewUrl,
  startStage2, pollStage2, stage2OverlayUrl,
  getConfig, editScale, getPricing, editRate, getEstimate,
  removeMaterial, undoEdit,
  listZones, deleteZone, restoreZone, deleteZonesBatch,
  getPoolScope,
} from "./api.js";
import ZoneEditor from "./ZoneEditor.jsx";
import PoolScopePanel from "./PoolScopePanel.jsx";
import PreviousJobs from "./PreviousJobs.jsx";

const STAGES = [
  { n: 1, name: "Upload & select pages", active: true },
  { n: 2, name: "Extract vector lines", active: false },
  { n: 3, name: "Measure square footage", active: false },
];

// --- per-zone swatch colors -------------------------------------------------
// Zones of one material share a base color; vary it per-zone (shade + slight hue
// wobble) so the list reads as MIXED shades instead of one flat color.
function hexToHsl(hex) {
  let c = (hex || "#888888").replace("#", "");
  if (c.length === 3) c = c.split("").map((x) => x + x).join("");
  const r = parseInt(c.slice(0, 2), 16) / 255;
  const g = parseInt(c.slice(2, 4), 16) / 255;
  const b = parseInt(c.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (d) {
    s = d / (1 - Math.abs(2 * l - 1));
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return { h, s: s * 100, l: l * 100 };
}
function zoneShade(baseHex, i) {
  const { h, s, l } = hexToHsl(baseHex);
  const dl = [0, 16, -12, 26, -20, 8, 34, -6, 20, -14][i % 10]; // lightness spread
  const dh = ((i % 3) - 1) * 9;                                  // small hue wobble
  const L = Math.min(82, Math.max(30, l + dl));
  const H = (h + dh + 360) % 360;
  const S = Math.min(95, Math.max(42, s));
  return `hsl(${H} ${S}% ${L}%)`;
}

export default function App({ onLogout }) {
  const [job, setJob] = useState(null);      // latest job status
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [eager, setEager] = useState(null);
  const [resumed, setResumed] = useState(false);  // re-uploaded the same PDF -> resumed prior job
  const [preview, setPreview] = useState(null); // {index, sheet, title} or null
  const [view, setView] = useState("stage1");   // "stage1" | "stage2"
  const [s2, setS2] = useState(null);           // stage 2 status/result
  const [s2page, setS2page] = useState(null);   // page index being detected
  const [config, setConfig] = useState(null);   // Gemini auto-config (reviewable)
  const [editMode, setEditMode] = useState(false); // interactive select on the overlay
  const [pricing, setPricing] = useState(null);  // costed estimate for the page
  const [estimate, setEstimate] = useState(null); // combined OE estimate (all pages)
  const [overlayKey, setOverlayKey] = useState(0); // cache-bust for the overlay image
  const [zones, setZones] = useState([]);          // active zones (id-addressable + geometry)
  const [deletedZones, setDeletedZones] = useState([]); // soft-deleted zones
  const [pageDims, setPageDims] = useState(null);  // base-page {width,height} for the SVG viewBox
  const [hoverZone, setHoverZone] = useState(null);     // zone id highlighted on the overlay
  const [maskPolys, setMaskPolys] = useState([]);  // optimistically-erased regions (instant delete)
  const fileRef = useRef(null);

  async function loadZones(page = s2page) {
    if (!job?.job_id || page == null) return;
    try {
      const res = await listZones(job.job_id, page, true);
      const all = res.zones || [];
      setZones(all.filter((z) => z.status === "active"));
      setDeletedZones(all.filter((z) => z.status === "deleted"));
      setPageDims(res.page || null);
    } catch { /* zones unavailable for this detection path */ }
  }

  function afterEdit(updated) {
    setS2(updated);
    setPricing(null);  // quantities changed -> pricing is stale
    setOverlayKey((k) => k + 1);  // force the overlay <img> to reload the new render
    loadZones();
  }

  // refresh the zone list whenever a page finishes detecting
  useEffect(() => {
    if (s2?.status === "done") loadZones();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s2?.status, s2page]);

  // Optimistic delete: drop the zones from the list, erase their regions on the
  // overlay (white mask), and shave their area off the material totals — all
  // instantly — then let the server re-render catch up in the background.
  function optimisticRemove(ids) {
    const idset = new Set(ids);
    const gone = zones.filter((z) => idset.has(z.id));
    if (!gone.length) return;
    setZones((prev) => prev.filter((z) => !idset.has(z.id)));
    setMaskPolys((prev) => [...prev, ...gone.flatMap((z) => z.geometry || [])]);
    const lost = {};
    for (const z of gone) lost[z.code] = (lost[z.code] || 0) + (z.area_sqft || 0);
    setS2((prev) => prev && ({
      ...prev,
      groups: (prev.groups || []).map((g) =>
        lost[g.label] ? { ...g, sqft: Math.max(0, (g.sqft || 0) - lost[g.label]) } : g),
    }));
  }
  function rollback(e) { setError(e.message); setMaskPolys([]); loadZones(); }

  async function handleDeleteZone(id) {
    optimisticRemove([id]);
    try { afterEdit(await deleteZone(job.job_id, id)); }
    catch (e) { rollback(e); }
  }
  async function handleRestoreZone(id) {
    try { afterEdit(await restoreZone(job.job_id, id)); }
    catch (e) { setError(e.message); }
  }

  async function handleDeleteIds(ids) {
    if (!ids || !ids.length) return;
    optimisticRemove(ids);
    try { afterEdit(await deleteZonesBatch(job.job_id, s2page, ids)); }
    catch (e) { rollback(e); }
  }
  async function handleRemoveMaterial(code) {
    optimisticRemove(zones.filter((z) => z.code === code).map((z) => z.id));
    try { afterEdit(await removeMaterial(job.job_id, s2page, code)); }
    catch (e) { rollback(e); }
  }
  async function handleUndo() {
    try { afterEdit(await undoEdit(job.job_id, s2page)); }
    catch (e) { setError(e.message); }
  }

  // load the combined OE estimate (all detected pages) when entering Stage 3
  useEffect(() => {
    if (view !== "stage3" || !job?.job_id) return;
    getEstimate(job.job_id).then(setEstimate).catch(() => {});
  }, [view, job]);

  async function onRate(code, val) {
    const n = parseFloat(val);
    if (!(n >= 0)) return;
    await editRate(job.job_id, s2page ?? 0, code, n);   // rates are per-job
    getEstimate(job.job_id).then(setEstimate).catch(() => {});  // refresh totals
  }

  // Download the combined OE estimate as a CSV (Stage 3): section, subsection, line.
  function downloadPricingCsv() {
    if (!estimate || !estimate.sections) return;
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [["Section", "Subsection", "Code", "Description", "Quantity", "Unit", "Rate ($)", "Cost ($)"]];
    const push = (sec, sub, r) => rows.push([sec, sub, r.code, r.name || "", r.qty, r.unit, r.rate, r.cost]);
    estimate.sections.forEach((sec) => {
      sec.subsections.forEach((s) => s.lines.forEach((r) => push(sec.name, s.name, r)));
      sec.lines.forEach((r) => push(sec.name, "", r));
    });
    rows.push(["", "", "", "", "", "", "GRAND TOTAL", estimate.grand_total]);
    const csv = rows.map((r) => r.map(esc).join(",")).join("\r\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `OE_estimate_${job.job_id}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // poll the Gemini auto-config once a job exists, until it's ready
  useEffect(() => {
    if (!job?.job_id || config) return;
    let alive = true;
    const tick = async () => {
      const c = await getConfig(job.job_id);
      if (alive && c) setConfig(c);
      else if (alive) setTimeout(tick, 1500);
    };
    tick();
    return () => { alive = false; };
  }, [job?.job_id, config]);

  async function handleFile(files) {
    const list = Array.from(files && files.length !== undefined ? files : [files]).filter(Boolean);
    if (!list.length) return;
    setError(null);
    setBusy(true);
    setJob(null);
    setView("stage1");
    setS2(null);
    setConfig(null);
    try {
      const up = await uploadPdf(list);
      setEager(up.eager);
      setResumed(!!up.resumed);
      await pollJob(up.job_id, (j) => setJob({ ...j, job_id: up.job_id }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Resume a previous job by job_id (from the Previous Jobs panel).
  async function handleResume(jobId) {
    setError(null);
    setBusy(true);
    setJob(null);
    setView("stage1");
    setS2(null);
    setConfig(null);
    try {
      await pollJob(jobId, (j) => setJob({ ...j, job_id: jobId }));
      setResumed(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Stage 2 — detect & color surface regions on a kept page.
  async function runStage2(pageIndex) {
    setS2page(pageIndex);
    setS2({ status: "queued" });
    setOverlayKey((k) => k + 1);  // fresh overlay URL for this detection
    try {
      await startStage2(job.job_id, pageIndex);
      await pollStage2(job.job_id, pageIndex, setS2);
    } catch (e) {
      setS2({ status: "error", error: e.message });
    }
  }

  function goStage2() {
    setView("stage2");
    const first = job.pages.find((p) => p.keep);
    runStage2(first ? first.index : 0);
  }

  const onDrop = (e) => {
    e.preventDefault();
    handleFile(e.dataTransfer.files);
  };

  const kept = job?.pages?.filter((p) => p.keep) ?? [];
  const poolPages = job?.pages?.filter((p) => !p.keep && p.pool_style) ?? [];
  const dropped = job?.pages?.filter((p) => !p.keep && !p.pool_style) ?? [];

  return (
    <div className="app">
      <header>
        <div className="header-row">
          <h1>Outdoor Elements — <span className="accent">AI Takeoff</span></h1>
          {onLogout && (
            <button className="logout-btn" onClick={onLogout} title="Log out">
              <span className="material-symbols-outlined">logout</span> Log out
            </button>
          )}
        </div>
        <p className="sub">
          The AI does what an estimator does, faster. Upload a drawing set and it
          keeps only the takeoff plan sheets.
        </p>
      </header>

      <Stages current={view === "stage3" ? 3 : view === "stage2" ? 2 : 1} done={job?.status === "done"} />

      {!job && (
        <div
          className={`dropzone ${busy ? "busy" : ""}`}
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => handleFile(e.target.files)}
          />
          {busy ? (
            <p>Working…</p>
          ) : (
            <>
              <p className="big">Drop the drawing PDF(s) here</p>
              <p className="muted">or click to browse · one or more vector PDFs — merged into one set</p>
            </>
          )}
        </div>
      )}

      {!job && !busy && <PreviousJobs onResume={handleResume} />}

      {error && <div className="error">⚠ {error}</div>}

      {job && job.status !== "done" && !error && (
        <section className="results">
          <p className="status" style={{ marginTop: 0 }}>Stage 1 · selecting takeoff pages…</p>
          <div className="grid">
            {[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton sk-card" />)}
          </div>
        </section>
      )}

      {job?.status === "done" && view === "stage1" && (
        <section className="results">
          {resumed && (
            <div className="resumed-banner">
              <span className="material-symbols-outlined">history</span>
              Resumed your previous session for this PDF — your earlier edits are kept.
            </div>
          )}
          <div className="summary">
            <strong>{job.filename}</strong> — {job.page_count} pages →{" "}
            <span className="kept-badge">{job.kept_count} required</span>
            {job.pool_style_count > 0 && (
              <span className="pool-badge">{job.pool_style_count} pool-style</span>
            )}
            <span className="engine">
              {eager ? "ran inline (no Redis)" : "ran on Celery worker"}
            </span>
          </div>

          <h2>Required pages ({kept.length})</h2>
          <div className="grid">
            {kept.map((p) => (
              <figure
                key={p.index}
                className="card keep"
                onClick={() => setPreview(p)}
                title="Click to enlarge"
              >
                <img src={thumbUrl(job.job_id, p.thumb)} alt={p.sheet} loading="lazy" />
                <figcaption>
                  <div className="sheet">{p.sheet} <span className="zoom">⤢</span></div>
                  <div className="title">{p.title}</div>
                  <div className="reason">{p.reason}</div>
                </figcaption>
              </figure>
            ))}
            {kept.length === 0 && <p className="muted">No color-coded takeoff plans found.</p>}
          </div>

          {poolPages.length > 0 && (
            <details className="more">
              <summary>{poolPages.length} pool-style sheets (need pool mode — next iteration)</summary>
              <ul>
                {poolPages.map((p) => (
                  <li key={p.index}>
                    p{p.index + 1} <b>{p.sheet}</b> — {p.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <details className="more">
            <summary>{dropped.length} dropped sheets</summary>
            <ul>
              {dropped.map((p) => (
                <li key={p.index}>
                  p{p.index + 1} <b>{p.sheet}</b> {p.title && `· ${p.title}`} — {p.reason}
                </li>
              ))}
            </ul>
          </details>

          <div className="actions">
            <button className="secondary" onClick={() => setJob(null)}>Upload another</button>
            <button className="primary" onClick={goStage2}>
              Continue → Stage 2
            </button>
          </div>
        </section>
      )}

      {job?.status === "done" && view === "stage2" && (
        <section className="results">
          <div className="s2bar">
            <button className="secondary" onClick={() => setView("stage1")}>← Back to pages</button>
            <span className="muted">Stage 2 · detect &amp; color surface areas (preview only)</span>
          </div>

          <div className="s2pages">
            {kept.map((p) => (
              <button
                key={p.index}
                className={`pagepick ${s2page === p.index ? "active" : ""}`}
                onClick={() => runStage2(p.index)}
              >
                {p.sheet}
              </button>
            ))}
            <span className="anypage">
              or detect page #
              <input
                type="number"
                min="1"
                max={job.page_count}
                defaultValue={(s2page ?? 0) + 1}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const n = parseInt(e.target.value, 10);
                    if (n >= 1 && n <= job.page_count) runStage2(n - 1);
                  }
                }}
              />
              <span className="muted">of {job.page_count} (Enter)</span>
            </span>
          </div>

          {(!s2 || s2.status === "queued" || s2.status === "running") && (
            <div className="s2grid">
              <div className="skeleton sk-img" />
              <div className="s2side">
                <div className="sk-head">
                  <div className="skeleton sk-tile" />
                  <div className="skeleton sk-line w60" style={{ margin: 0 }} />
                </div>
                <div className="skeleton sk-line w80" />
                <div className="skeleton sk-line" />
                <div className="skeleton sk-line w60" />
                <div className="skeleton sk-line w40" />
                <p className="muted" style={{ marginTop: 14 }}>Detecting surface areas…</p>
              </div>
            </div>
          )}
          {s2?.status === "error" && <div className="error">⚠ {s2.error}</div>}

          {s2?.status === "done" && (
            <div className="s2grid">
              <ZoneEditor
                imgUrl={`${stage2OverlayUrl(job.job_id, s2page)}?v=${s2page}-${overlayKey}`}
                zones={zones}
                page={pageDims}
                editMode={editMode}
                highlightId={hoverZone}
                onDeleteIds={handleDeleteIds}
                maskPolys={maskPolys}
                onOverlayLoad={() => setMaskPolys([])}
              />

              <div className="s2side">
                <div className="edit-bar">
                  <button
                    className={`edit-toggle ${editMode ? "on" : ""}`}
                    onClick={() => setEditMode((v) => !v)}
                  >
                    <span className="material-symbols-outlined">{editMode ? "check" : "edit"}</span>
                    {editMode ? "Edit mode" : "Edit"}
                  </button>
                  <button className="ghost" disabled={!s2.can_undo} onClick={handleUndo}>
                    <span className="material-symbols-outlined">undo</span> Undo
                  </button>
                </div>
                {editMode && <p className="muted edit-hint">Click zones to select (click again to deselect), then Delete. Or use the 🗑 on a material to drop all of it.</p>}
                {s2.edit_note && <p className="edit-note">{s2.edit_note}</p>}

                {s2?.method === "pool" && (
                  <PoolScopePanel jobId={job.job_id} />
                )}
                {(s2.groups?.length > 0 || zones.length > 0 || deletedZones.length > 0) ? (() => {
                  // One combined column: each material is a header row with its total,
                  // and its individual zones are nested beneath it (was two separate
                  // "Materials" + "Zones" lists).
                  const byCode = {};
                  for (const z of zones) (byCode[z.code] ||= []).push(z);
                  const groups = s2.groups || [];
                  const seen = new Set(groups.map((g) => g.label));
                  const extra = Object.keys(byCode)
                    .filter((c) => !seen.has(c))
                    .map((c) => ({ label: c, hex: byCode[c][0]?.hex, sqft: null }));
                  const ordered = [...groups, ...extra];
                  return (
                    <div className="zones-block">
                      <div className="panel-head">
                        <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">layers</span></span>
                        <h3>Surfaces <span className="muted">({zones.length} zones · {ordered.length} materials)</span></h3>
                      </div>
                      {s2.message && <p className="muted s2msg">{s2.message}</p>}
                      <p className="hint">Each material rolls up its individual zones — hover a zone to highlight it, delete it by id. Square footage = vector geometry × the sheet scale.</p>
                      {ordered.length > 0 ? (
                        <ul className="surflist">
                          {ordered.map((g) => (
                            <li key={g.label || g.hex} className="matgroup">
                              <div className="matrow">
                                <span className="swatch" style={{ background: g.hex }} />
                                <code>{g.label || g.hex}</code>
                                {g.sqft != null
                                  ? <span className="sqft">{g.sqft.toLocaleString()} sq ft</span>
                                  : <span className="sqft muted">—</span>}
                                {editMode && g.label && (
                                  <button
                                    className="trash" title={`Remove all ${g.label}`}
                                    aria-label={`Remove all ${g.label}`}
                                    onClick={() => handleRemoveMaterial(g.label)}
                                  ><span className="material-symbols-outlined">delete</span></button>
                                )}
                              </div>
                              {(byCode[g.label] || []).length > 0 && (
                                <ul className="zonelist nested">
                                  {(byCode[g.label] || []).map((z, zi) => (
                                    <li
                                      key={z.id} className={`zonerow ${hoverZone === z.id ? "hot" : ""}`}
                                      onMouseEnter={() => setHoverZone(z.id)}
                                      onMouseLeave={() => setHoverZone((h) => (h === z.id ? null : h))}
                                    >
                                      <span className="swatch" style={{ background: zoneShade(z.hex || g.hex, zi) }} />
                                      {z.area_sqft ? <span className="sqft">{z.area_sqft.toLocaleString()} sq ft</span> : null}
                                      <button
                                        className="trash" title={`Delete zone ${z.id.slice(0, 6)}`}
                                        aria-label={`Delete zone ${z.id.slice(0, 6)}`}
                                        onClick={() => handleDeleteZone(z.id)}
                                      ><span className="material-symbols-outlined">delete</span></button>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted">No colored surfaces on this page.</p>
                      )}
                      {deletedZones.length > 0 && (
                        <details className="deleted-zones">
                          <summary>Deleted ({deletedZones.length})</summary>
                          <ul className="zonelist">
                            {deletedZones.map((z) => (
                              <li key={z.id} className="zonerow gone">
                                <span className="swatch" style={{ background: z.hex }} />
                                <code>{z.code}</code>
                                <span className="zid">#{z.id.slice(0, 6)}</span>
                                {z.area_sqft ? <span className="sqft">{z.area_sqft.toLocaleString()} sq ft</span> : null}
                                <button
                                  className="ghost restore" title="Restore zone"
                                  onClick={() => handleRestoreZone(z.id)}
                                ><span className="material-symbols-outlined">undo</span></button>
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </div>
                  );
                })() : null}

                {Array.isArray(s2.takeoff) && s2.takeoff.some((t) => t.unit !== "area" && t.quantity && t.source !== "planting") && (
                  <div className="zones-block">
                    <div className="panel-head">
                      <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">straighten</span></span>
                      <h3>Walls &amp; Counts <span className="muted">(linear &amp; count)</span></h3>
                    </div>
                    <p className="hint">Measured by the per-material brain — walls/borders in linear feet, benches/columns as counts.</p>
                    <ul className="surflist">
                      {s2.takeoff.filter((t) => t.unit !== "area" && t.quantity && t.source !== "planting").map((t) => (
                        <li key={`${t.code}-${t.name}`} className="zonerow">
                          <span className={`unit-chip ${t.unit}`}>{t.unit === "linear" ? "LF" : "EA"}</span>
                          <code>{t.code}</code>
                          <span className="tk-name muted">{t.name}</span>
                          <span className="sqft">{t.quantity.toLocaleString()} {t.unit_label}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {Array.isArray(s2.takeoff) && s2.takeoff.some((t) => t.source === "planting" && t.quantity) && (
                  <div className="zones-block">
                    <div className="panel-head">
                      <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">forest</span></span>
                      <h3>Plants <span className="muted">({s2.takeoff.filter((t) => t.source === "planting" && t.quantity).reduce((n, t) => n + t.quantity, 0).toLocaleString()} total)</span></h3>
                    </div>
                    <p className="hint">Per-species counts — schedule-anchored, via the visual model (gemini-3.1-pro).</p>
                    <ul className="surflist">
                      {s2.takeoff.filter((t) => t.source === "planting" && t.quantity).map((t) => (
                        <li key={`pl-${t.code}`} className="zonerow">
                          <span className="unit-chip count">EA</span>
                          <code>{t.code}</code>
                          <span className="tk-name muted">{t.name}</span>
                          <span className="sqft">{t.quantity.toLocaleString()} each</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          )}

          {s2?.status === "done" && (
            <div className="actions">
              <button className="secondary" onClick={() => setView("stage1")}>← Pages</button>
              <button className="primary" onClick={() => setView("stage3")}>
                Continue → Stage 3 (measure &amp; compare)
              </button>
            </div>
          )}
        </section>
      )}

      {job?.status === "done" && view === "stage1" && (
        <ConfigPanel jobId={job.job_id} config={config} setConfig={setConfig} />
      )}

      {job?.status === "done" && view === "stage3" && (
        <section className="results">
          <div className="s2bar">
            <span className="muted">Stage 3 · estimate · Outdoor Elements scope of work</span>
          </div>

          {!estimate || !estimate.sections || estimate.sections.length === 0 ? (
            <div className="status">No priced takeoff yet — detect material pages in Stage 2, then return here.</div>
          ) : (() => {
            const money = (v) => `$${Math.round(v).toLocaleString()}`;
            const line = (r) => (
              <li key={r.code} className="oe-line">
                <span className="oe-line-main">
                  <code className="oe-code">{r.code}</code>
                  <span className="oe-desc">{r.name || r.code}</span>
                  <span className="oe-meta">
                    {r.qty.toLocaleString()} {r.unit} @ $
                    <input
                      className="rate-in" type="number" min="0" step="0.5"
                      defaultValue={r.rate}
                      onBlur={(e) => onRate(r.code, e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
                    />
                  </span>
                </span>
                <span className="oe-cost">{money(r.cost)}</span>
              </li>
            );
            return (
              <section className="pricing oe-estimate">
                <div className="panel-head">
                  <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">request_quote</span></span>
                  <h3>Estimate <span className="muted">· Outdoor Elements scope · all {estimate.page_count} detected page(s)</span></h3>
                  <button className="csv-btn" onClick={downloadPricingCsv} title="Download as CSV">
                    <span className="material-symbols-outlined">download</span> CSV
                  </button>
                </div>
                {estimate.sections.map((sec) => (
                  <div className="oe-section" key={sec.name}>
                    <div className="oe-sec-head">
                      <span className="oe-sec-name">{sec.name}</span>
                      <span className="oe-sec-total">{money(sec.total)}</span>
                    </div>
                    {sec.subsections.map((s) => (
                      <div className="oe-sub" key={s.name}>
                        <div className="oe-sub-head"><span>{s.name}</span><span>{money(s.total)}</span></div>
                        <ul className="oe-lines">{s.lines.map(line)}</ul>
                      </div>
                    ))}
                    {sec.lines.length > 0 && <ul className="oe-lines">{sec.lines.map(line)}</ul>}
                  </div>
                ))}
                <div className="oe-grand">
                  <span>GRAND TOTAL</span>
                  <span>{money(estimate.grand_total)}</span>
                </div>
                <p className="hint">
                  Combined across all detected pages, in the Outdoor Elements scope-of-work format.
                  Edit any rate (Enter) — the line, section and grand total update.
                </p>
              </section>
            );
          })()}
        </section>
      )}

      {preview && (
        <div className="lightbox" onClick={() => setPreview(null)}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <header className="lightbox-bar">
              <span>
                <b>{preview.sheet}</b> · {preview.title}
              </span>
              <button className="close" aria-label="Close preview" onClick={() => setPreview(null)}>
                <span className="material-symbols-outlined">close</span>
              </button>
            </header>
            <div className="lightbox-img">
              <img src={previewUrl(job.job_id, preview.index)} alt={preview.sheet} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ConfigPanel({ jobId, config, setConfig }) {
  if (!config) {
    return (
      <section className="config-panel">
        <div className="panel-head">
          <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">tune</span></span>
          <h3>Auto-config <span className="spin">◴</span></h3>
        </div>
        <p className="muted">Reading the drawing with Gemini — sheets, scale, materials…</p>
        <div className="skeleton sk-line w80" />
        <div className="skeleton sk-line w60" />
        <div className="skeleton sk-line w40" />
      </section>
    );
  }
  const sheets = Object.entries(config.sheets || {});
  const mats = Object.entries(config.materials || {});

  async function onScale(sheetId, denom) {
    const n = parseFloat(denom);
    if (!n || n <= 0) return;
    const updated = await editScale(jobId, sheetId, 1 / n);
    setConfig(updated);
  }

  return (
    <section className="config-panel">
      <div className="panel-head">
        <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">tune</span></span>
        <h3>
          Detected config — review &amp; correct
          {config.source === "fallback" && <span className="warn"> · fallback (Gemini failed)</span>}
        </h3>
      </div>
      <p className="muted">
        Scale per sheet drives the area math. If a printed scale is misleading, fix the denominator
        (e.g. L1.01 here should be 1/16, not 1/10).
      </p>
      <table className="cfg-table">
        <thead><tr><th>Sheet</th><th>Title</th><th>Scale (1 / N″ = 1′)</th><th>Page</th></tr></thead>
        <tbody>
          {sheets.map(([sid, info]) => (
            <tr key={sid}>
              <td><code>{sid}</code></td>
              <td>{info.title}</td>
              <td>
                1 /{" "}
                <input
                  className="scale-in"
                  type="number" min="1" step="1"
                  defaultValue={Math.round(1 / info.scale_in_per_ft)}
                  onBlur={(e) => onScale(sid, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") onScale(sid, e.target.value); }}
                />″ = 1′
              </td>
              <td>{info.page}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="more">
        <summary>{mats.length} materials</summary>
        <ul>{mats.map(([c, n]) => <li key={c}><b>{c}</b> — {n}</li>)}</ul>
      </details>
    </section>
  );
}

function Stages({ current, done }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STAGES.map((s, i) => {
        const state =
          s.n < current || (s.n === current && done)
            ? "done"
            : s.n === current
            ? "current"
            : "todo";
        return (
          <li key={s.n} className={`stp ${state}`} aria-current={state === "current" ? "step" : undefined}>
            {i > 0 && <span className={`stp-line ${s.n <= current ? "fill" : ""}`} aria-hidden="true" />}
            <span className="stp-dot">
              {state === "done"
                ? <span className="material-symbols-outlined">check</span>
                : s.n}
            </span>
            <span className="stp-label">{s.name}</span>
          </li>
        );
      })}
    </ol>
  );
}
