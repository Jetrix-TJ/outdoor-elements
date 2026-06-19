"""End-to-end surface-area takeoff (Outdoor Elements POC).

    python run_takeoff.py                 # sheet L1.01 (page 0)
    python run_takeoff.py --page 1        # another sheet by 0-based page
    python run_takeoff.py --all           # every page that has sq-ft legend items
    python run_takeoff.py --page 1 --scale 8   # force feet-per-inch if OCR is unsure

Pipeline (mirrors Grodsky's staged structure):
  s2 extract -> s4 calibrate (per-sheet scale) -> s3 legend (dynamic) ->
  s5 detect (vector polygons, markers removed) -> s6 Gemini validate/split ->
  s7 annotate -> s8 compare to the legend's printed QTO values.

Outputs land in ./outputs (per sheet); RESULTS.md summarizes the run.
"""
from __future__ import annotations

import argparse
import csv
import json
from itertools import product

from oe_takeoff import config
from oe_takeoff import (s2_extract, s3_legend, s4_calibrate, s5_segment,
                        s6_validate, s7_annotate, s8_compare)

# Raster baseline sweep (only run on L1.01 as the documented "before" story).
SWEEP = {"rgb_tol": [18, 34], "close_px": [3, 7], "min_region_sqft": [3.0]}
SCORING_CODES = ["M.5", "M.16"]


def _slug(label: str) -> str:
    return label.replace(".", "_").replace("/", "_").replace(" ", "")


def _mape_vs_section4(seg, codes) -> float:
    errs = []
    for code in codes:
        gt = config.SECTION4_GROUND_TRUTH[code]["sqft"]
        errs.append(abs(seg.get(code, {}).get("sqft", 0.0) - gt) / gt * 100.0)
    return sum(errs) / len(errs) if errs else 0.0


def run_sheet(page: int, scale_override: float | None, emit) -> dict | None:
    emit(f"[s2] Extracting page {page} at {config.RENDER_DPI} DPI ...")
    ctx = s2_extract.extract(page)
    emit(f"     rendered {ctx['rgb'].shape[1]}x{ctx['rgb'].shape[0]} px; "
         f"{len(ctx['words'])} words; {len(ctx['drawings'])} vector paths")

    emit("[s4] Calibrating scale ...")
    calib = s4_calibrate.calibrate(ctx, feet_per_inch=scale_override)
    for n in calib["notes"]:
        emit("     " + n)
    default_label = config.DEFAULT_SHEET if page == 0 else f"p{page}"
    sheet = calib.get("sheet_read") or default_label
    ctx["sheet"] = sheet

    emit("[s3] Reading legend ...")
    items, legend_notes = s3_legend.read_legend(ctx)
    for n in legend_notes:
        emit("     " + n)
    if not items:
        emit(f"     No sq-ft (area) legend items on {sheet}; skipping.")
        return None

    raster_mape = None
    if page == 0:  # L1.01 baseline narrative
        emit("[s5a] Raster color-segmentation sweep (baseline) ...")
        best = None
        for tol, close_px, min_sqft in product(SWEEP["rgb_tol"], SWEEP["close_px"], SWEEP["min_region_sqft"]):
            seg_r = s5_segment.segment_raster(ctx, items, calib, rgb_tol=tol,
                                              close_px=close_px, min_region_sqft=min_sqft)
            score = _mape_vs_section4(seg_r, SCORING_CODES)
            if best is None or score < best["score"]:
                best = {"score": score, "seg": seg_r}
        raster_mape = _mape_vs_section4(best["seg"], config.TARGET_CODES)
        emit(f"      best raster baseline MAPE {raster_mape:.1f}% "
             f"(color can't split M.6/M.15; M.7 vs linework)")

    emit("[s5b] Vector polygon detection (markers removed) ...")
    seg = s5_segment.detect_vector(ctx, items, calib)
    for it in items:
        info = seg[it.code]
        flag = f" [{info.get('note')}]" if info.get("note") else ""
        emit(f"      {it.code} {it.name[:18]:18s} fill={info['fill_rgb']} "
             f"regions={len(info['regions'])} mk={info.get('markers_removed',0)} "
             f"-> {info['sqft']:.1f} sqft{flag}")

    emit("[s6] Gemini validation / shared-color split ...")
    seg, v_notes = s6_validate.validate(ctx, seg, calib, items)
    for n in v_notes:
        emit("     " + n)

    emit("[s8] Building rows + comparison ...")
    rows = s8_compare.build_rows(seg, items, sheet)
    notes = calib["notes"] + legend_notes + v_notes
    if raster_mape is not None:
        notes.append(f"raster baseline MAPE {raster_mape:.1f}% -> vector (final)")
    report = s8_compare.compare(rows, items, calib, sheet, notes)

    emit("[s7] Annotating sheet ...")
    annotated = s7_annotate.annotate(ctx, seg, rows, items, f"annotated_{_slug(sheet)}.png")
    emit(f"     wrote {annotated}")

    # outputs
    stem = _slug(sheet)
    payload = [r.model_dump() for r in rows]
    (config.OUTPUT_DIR / f"output_{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (config.OUTPUT_DIR / f"output_{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(payload[0].keys()))
        w.writeheader(); w.writerows(payload)

    rp = report.model_dump()
    rp["detector"] = "vector"
    rp["feet_per_inch"] = calib["feet_per_inch"]
    rp["raster_mape"] = raster_mape
    rp["vector_mape"] = report.mape
    rp["fill_rgb"] = {it.code: list(seg.get(it.code, {}).get("fill_rgb") or []) for it in items}
    rp["region_count"] = {it.code: len(seg.get(it.code, {}).get("regions", [])) for it in items}
    rp["flags"] = {it.code: (seg.get(it.code, {}).get("note") or "") for it in items}
    (config.OUTPUT_DIR / f"report_{stem}.json").write_text(json.dumps(rp, indent=2), encoding="utf-8")
    emit(f"     wrote outputs/output_{stem}.json/.csv and report_{stem}.json")

    print(s8_compare.format_table(report))
    return {"sheet": sheet, "page": page, "stem": stem, "mape": report.mape,
            "report": rp, "annotated": str(annotated)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Outdoor Elements surface-area takeoff")
    ap.add_argument("--page", type=int, default=None, help="0-based page index")
    ap.add_argument("--all", action="store_true", help="process every page with area items")
    ap.add_argument("--scale", type=float, default=None, help="feet per inch override")
    args = ap.parse_args()

    log: list[str] = []
    def emit(msg: str) -> None:
        print(msg); log.append(msg)

    if args.all:
        import fitz
        doc = fitz.open(config.PDF_PATH); n = doc.page_count; doc.close()
        pages = list(range(n))
    else:
        pages = [args.page if args.page is not None else config.DEFAULT_PAGE_INDEX]

    results = []
    for pg in pages:
        emit(f"\n========== PAGE {pg} ==========")
        try:
            r = run_sheet(pg, args.scale, emit)
        except Exception as e:
            emit(f"     ERROR on page {pg}: {type(e).__name__}: {e}")
            r = None
        if r:
            results.append(r)

    # manifest for the UI + RESULTS
    manifest = [{"sheet": r["sheet"], "stem": r["stem"], "page": r["page"], "mape": r["mape"]}
                for r in results]
    (config.OUTPUT_DIR / "sheets.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_results(results, log)
    emit(f"\nDone. Sheets processed: {', '.join(r['sheet'] for r in results) or 'none'}")


def _write_results(results, log) -> None:
    lines = ["# RESULTS - Surface-Area Takeoff", ""]
    for r in results:
        rep = r["report"]
        lines.append(f"## {r['sheet']}  (scale 1\" = {rep.get('feet_per_inch')}', "
                     f"MAPE {rep['mape']:.1f}%)")
        lines += ["", "| Code | Name | Ground truth | Measured | Error % | Flag |",
                  "|------|------|-------------:|---------:|--------:|------|"]
        for c in rep["rows"]:
            flag = rep.get("flags", {}).get(c["code"], "") or ""
            lines.append(f"| {c['code']} | {c['name']} | {c['ground_truth']:,.2f} | "
                         f"{c['measured']:,.2f} | {c['error_pct']:+.1f}% | {flag} |")
        if rep.get("raster_mape") is not None:
            lines.append("")
            lines.append(f"_Raster color-seg baseline: {rep['raster_mape']:.0f}% MAPE -> "
                         f"vector polygons: {rep['mape']:.1f}% MAPE._")
        lines.append("")
    lines += ["## Method", "",
              "- Calibration: pages are true paper size; scale read per sheet (Gemini OCR of",
              "  the title block) -> `pixels_per_foot = DPI / feet_per_inch`.",
              "- Detection: exact PDF **vector fill polygons**; each material's true fill color",
              "  is resolved from its legend swatch rectangle. Repeated identical small square",
              "  fills (symbol/keynote markers) are excluded.",
              "- Shared fill colors (two materials, one color) are split by Gemini region",
              "  classification.",
              "- Ground truth = each legend row's printed QTO value (Section 4 for L1.01).",
              "", "## Run log", "", "```", *log, "```"]
    (config.PROJECT_DIR / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
