import { useEffect, useRef, useState } from "react";
import {
  uploadPdf, pollJob, thumbUrl, previewUrl,
  startStage2, pollStage2, stage2OverlayUrl,
  getConfig, editScale, getPricing, editRate,
  pickZone, removeMaterial, undoEdit, removeBatch,
} from "./api.js";

const STAGES = [
  { n: 1, name: "Upload & select pages", active: true },
  { n: 2, name: "Extract vector lines", active: false },
  { n: 3, name: "Measure square footage", active: false },
];

export default function App() {
  const [job, setJob] = useState(null);      // latest job status
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [eager, setEager] = useState(null);
  const [preview, setPreview] = useState(null); // {index, sheet, title} or null
  const [view, setView] = useState("stage1");   // "stage1" | "stage2"
  const [s2, setS2] = useState(null);           // stage 2 status/result
  const [s2page, setS2page] = useState(null);   // page index being detected
  const [config, setConfig] = useState(null);   // Gemini auto-config (reviewable)
  const [editMode, setEditMode] = useState(false); // click-to-select on the overlay
  const [picks, setPicks] = useState([]);    // selected zones [{code, area, bbox, fx, fy}]
  const [pricing, setPricing] = useState(null);  // costed estimate for the page
  const [overlayKey, setOverlayKey] = useState(0); // cache-bust for the overlay image
  const fileRef = useRef(null);

  function afterEdit(updated) {
    setS2(updated);
    setPicks([]);
    setPricing(null);  // quantities changed -> pricing is stale
    setOverlayKey((k) => k + 1);  // force the overlay <img> to reload the new render
  }

  const inBox = (p, fx, fy) =>
    fx >= p.bbox[0] && fx <= p.bbox[2] && fy >= p.bbox[1] && fy <= p.bbox[3];

  async function handlePick(fx, fy) {
    const hit = picks.findIndex((p) => inBox(p, fx, fy));
    if (hit >= 0) { setPicks(picks.filter((_, i) => i !== hit)); return; } // toggle off
    try {
      const r = await pickZone(job.job_id, s2page, fx, fy);
      if (r.code) setPicks((ps) => [...ps, { ...r, fx, fy }]);
    } catch (e) { setError(e.message); }
  }
  async function deleteSelected() {
    try {
      afterEdit(await removeBatch(job.job_id, s2page, picks.map((p) => ({ x: p.fx, y: p.fy }))));
    } catch (e) { setError(e.message); }
  }
  async function handleRemoveMaterial(code) {
    try { afterEdit(await removeMaterial(job.job_id, s2page, code)); }
    catch (e) { setError(e.message); }
  }
  async function handleUndo() {
    try { afterEdit(await undoEdit(job.job_id, s2page)); }
    catch (e) { setError(e.message); }
  }

  // load pricing when entering Stage 3 (or after an edit clears it)
  useEffect(() => {
    if (view !== "stage3" || s2?.status !== "done" || pricing || !job?.job_id) return;
    getPricing(job.job_id, s2page).then(setPricing).catch(() => {});
  }, [view, s2, pricing, job, s2page]);

  async function onRate(code, val) {
    const n = parseFloat(val);
    if (!(n >= 0)) return;
    setPricing(await editRate(job.job_id, s2page, code, n));
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

  async function handleFile(file) {
    if (!file) return;
    setError(null);
    setBusy(true);
    setJob(null);
    setView("stage1");
    setS2(null);
    setConfig(null);
    try {
      const up = await uploadPdf(file);
      setEager(up.eager);
      await pollJob(up.job_id, (j) => setJob({ ...j, job_id: up.job_id }));
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
    handleFile(e.dataTransfer.files?.[0]);
  };

  const kept = job?.pages?.filter((p) => p.keep) ?? [];
  const poolPages = job?.pages?.filter((p) => !p.keep && p.pool_style) ?? [];
  const dropped = job?.pages?.filter((p) => !p.keep && !p.pool_style) ?? [];

  return (
    <div className="app">
      <header>
        <h1>Outdoor Elements — <span className="accent">AI Takeoff</span></h1>
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
            hidden
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          {busy ? (
            <p>Working…</p>
          ) : (
            <>
              <p className="big">Drop the drawing PDF here</p>
              <p className="muted">or click to browse · vector PDF, no AI needed for this step</p>
            </>
          )}
        </div>
      )}

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
              <div className={`s2img ${editMode ? "editing" : ""}`}>
                <img
                  src={`${stage2OverlayUrl(job.job_id, s2page)}?v=${s2page}-${overlayKey}`}
                  alt="detected surfaces"
                  onClick={(e) => {
                    if (!editMode) return;
                    const r = e.currentTarget.getBoundingClientRect();
                    handlePick((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
                  }}
                />
                {editMode && picks.map((p, i) => (
                  <div
                    key={i} className="pick-box"
                    style={{
                      left: `${p.bbox[0] * 100}%`, top: `${p.bbox[1] * 100}%`,
                      width: `${(p.bbox[2] - p.bbox[0]) * 100}%`,
                      height: `${(p.bbox[3] - p.bbox[1]) * 100}%`,
                    }}
                  >
                    <span className="pick-tag">{p.code}</span>
                  </div>
                ))}

                {/* Vortex-style floating selection bar */}
                {editMode && picks.length > 0 && (
                  <div className="select-bar">
                    <span className="sel-count">{picks.length} selected</span>
                    <span className="sel-div" />
                    <button className="sel-del" onClick={deleteSelected}>
                      <span className="material-symbols-outlined">delete</span> Delete
                    </button>
                    <button className="sel-clr" onClick={() => setPicks([])}>
                      <span className="material-symbols-outlined">close</span> Clear
                    </button>
                  </div>
                )}
              </div>

              <div className="s2side">
                <div className="edit-bar">
                  <button
                    className={`edit-toggle ${editMode ? "on" : ""}`}
                    onClick={() => { setEditMode((v) => !v); setPicks([]); }}
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

                <div className="panel-head">
                  <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">layers</span></span>
                  <h3>Materials <span className="muted">({s2.groups?.length || 0})</span></h3>
                </div>
                {s2.message && <p className="muted s2msg">{s2.message}</p>}
                {s2.groups?.length > 0 ? (
                  <ul className="s2legend">
                    {s2.groups.map((g) => (
                      <li key={g.hex + (g.label || "")} className="matrow">
                        <span className="swatch" style={{ background: g.hex }} />
                        <code>{g.label || g.hex}</code>
                        {g.sqft ? <span className="sqft">{g.sqft.toLocaleString()} sq ft</span> : null}
                        {editMode && g.label && (
                          <button
                            className="trash" title={`Remove all ${g.label}`}
                            aria-label={`Remove all ${g.label}`}
                            onClick={() => handleRemoveMaterial(g.label)}
                          ><span className="material-symbols-outlined">delete</span></button>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No colored surfaces on this page.</p>
                )}
                <p className="hint">Square footage measured from the vector geometry × the sheet scale.</p>
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
            <button className="secondary" onClick={() => setView("stage2")}>← Back to Stage 2</button>
            <span className="muted">Stage 3 · measure square footage · our output vs human QTO</span>
          </div>

          {!s2 || s2.status !== "done" ? (
            <div className="status">Run Stage 2 first.</div>
          ) : (
            <>
              {s2.comparison ? (
                <>
                  <div className={`verdict ${s2.comparison.mape != null && s2.comparison.mape < 5 ? "match" : "near"}`}>
                    {s2.comparison.mape != null
                      ? `Our takeoff vs human QTO — overall error (MAPE) ${s2.comparison.mape}% across ${s2.comparison.matched} matched materials`
                      : "Measured — no overlapping ground-truth values to score"}
                  </div>
                  <div className="s2grid">
                    <div className="s2img">
                      <img src={`${stage2OverlayUrl(job.job_id, s2page)}?v=${s2page}-${overlayKey}`} alt="measured surfaces" />
                    </div>
                    <div className="s2side">
                      <table className="cmp3">
                        <thead>
                          <tr><th>Material</th><th>Human</th><th>Ours</th><th>Δ</th></tr>
                        </thead>
                        <tbody>
                          {s2.comparison.rows.map((r) => {
                            const e = r.error_pct;
                            const cls = e == null ? "" : Math.abs(e) < 5 ? "ok" : Math.abs(e) < 15 ? "warn" : "bad";
                            return (
                              <tr key={r.code}>
                                <td><code>{r.code}</code> {r.name}</td>
                                <td>{r.ground_truth.toLocaleString()}</td>
                                <td>{r.measured != null ? Math.round(r.measured).toLocaleString() : "—"}</td>
                                <td className={cls}>{e == null ? "—" : `${e > 0 ? "+" : ""}${e}%`}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <p className="hint">
                        Human = sq-ft values printed in the sheet legend (the human takeoff).
                        Ours = measured from the vector geometry × scale.
                      </p>
                    </div>
                  </div>
                </>
              ) : s2.validation && s2.validation.length ? (
                <>
                  <div className={`verdict ${s2.validation.every((v) => v.delta_pct == null || Math.abs(v.delta_pct) < 10) ? "match" : "near"}`}>
                    Engine takeoff vs QTO reference — {s2.validation.filter((v) => v.delta_pct != null && Math.abs(v.delta_pct) < 10).length}/{s2.validation.length} within 10%
                  </div>
                  <div className="s2grid">
                    <div className="s2img">
                      <img src={`${stage2OverlayUrl(job.job_id, s2page)}?v=${s2page}-${overlayKey}`} alt="measured surfaces" />
                    </div>
                    <div className="s2side">
                      <table className="cmp3">
                        <thead><tr><th>Material</th><th>QTO ref</th><th>Ours</th><th>Δ</th></tr></thead>
                        <tbody>
                          {s2.validation.map((v) => {
                            const e = v.delta_pct;
                            const cls = e == null ? "" : Math.abs(e) < 5 ? "ok" : Math.abs(e) < 15 ? "warn" : "bad";
                            return (
                              <tr key={v.code}>
                                <td><code>{v.code}</code></td>
                                <td>{v.reference.toLocaleString()}</td>
                                <td>{v.computed != null ? Math.round(v.computed).toLocaleString() : "—"}</td>
                                <td className={cls}>{e == null ? "—" : `${e > 0 ? "+" : ""}${e}%`}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <p className="hint">
                        QTO ref = the human takeoff values. Ours = line-width zone engine
                        (M.5/M.7 within ~2%; some materials over-claim — a known engine limit).
                      </p>
                    </div>
                  </div>
                </>
              ) : (
                <div className="note-box">
                  <p><b>No human ground-truth on this sheet to compare against.</b></p>
                  <p className="muted">
                    The raw drawing's legend prints material names but no sq-ft values — the human
                    takeoff numbers live only in the <b>QTO</b>. Upload <code>2811 KIRBY QTO.pdf</code>
                    and run Stage 2 → Stage 3 to see our output line up against the human QTO.
                  </p>
                  <table className="cmp3">
                    <thead><tr><th>Material</th><th>Our measure (sq ft)</th></tr></thead>
                    <tbody>
                      {(s2.groups || []).map((g) => (
                        <tr key={g.label || g.hex}>
                          <td><code>{g.label || g.hex}</code></td>
                          <td>{g.sqft ? g.sqft.toLocaleString() : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {s2?.status === "done" && pricing && (
            <section className="pricing">
              <div className="panel-head">
                <span className="card-tile" aria-hidden="true"><span className="material-symbols-outlined">request_quote</span></span>
                <h3>Costed estimate <span className="muted">· quantity × unit rate</span></h3>
              </div>
              <table className="price-table">
                <thead>
                  <tr><th>Material</th><th>Qty</th><th>Unit</th><th>Rate ($)</th><th>Cost ($)</th></tr>
                </thead>
                <tbody>
                  {pricing.rows.map((r) => (
                    <tr key={r.code}>
                      <td><code>{r.code}</code> {r.name}</td>
                      <td>{r.qty.toLocaleString()}</td>
                      <td>{r.unit}</td>
                      <td>
                        $<input
                          className="rate-in" type="number" min="0" step="0.5"
                          defaultValue={r.rate}
                          onBlur={(e) => onRate(r.code, e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
                        />
                      </td>
                      <td className="cost">${r.cost.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={4}><b>Total</b></td>
                    <td className="total">${pricing.total.toLocaleString()}</td>
                  </tr>
                </tfoot>
              </table>
              <p className="hint">
                Starter $/sq-ft rates — edit any rate (Enter) and the cost + total update.
                Quantities reflect any zones you removed in Stage 2.
              </p>
            </section>
          )}
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
    <ol className="stages">
      {STAGES.map((s) => {
        const state =
          s.n < current || (s.n === current && done)
            ? "done"
            : s.n === current
            ? "current"
            : "todo";
        return (
          <li key={s.n} className={state}>
            <span className="dot">
              {state === "done"
                ? <span className="material-symbols-outlined" style={{ fontSize: 16 }}>check</span>
                : s.n}
            </span>
            {s.name}
          </li>
        );
      })}
    </ol>
  );
}
