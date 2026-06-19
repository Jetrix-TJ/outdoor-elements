"""Outdoor Elements - surface-area takeoff review UI.

    streamlit run app.py

Reads the artifacts produced by run_takeoff.py (outputs/) and shows, per sheet,
the annotated drawing, the measured-vs-ground-truth comparison, and the run
log. Read-only review surface - no web/local storage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"

st.set_page_config(page_title="Outdoor Elements - Takeoff", layout="wide")


def _sheets() -> list[dict]:
    mf = OUT / "sheets.json"
    if mf.exists():
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m:
            return m
    found = []
    for p in sorted(OUT.glob("report_*.json")):
        stem = p.stem.replace("report_", "")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            found.append({"sheet": d.get("sheet", stem), "stem": stem,
                          "page": d.get("page", 0), "mape": d.get("mape", 0)})
        except Exception:
            pass
    return found


def _load_report(stem: str) -> dict | None:
    p = OUT / f"report_{stem}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _chip(rgb) -> str:
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})" if rgb and len(rgb) == 3 else ""


st.title("Outdoor Elements — Surface-Area Takeoff")
st.caption("AI takeoff vs human QTO · detected from the PDF vector layer, validated by Gemini")

sheets = _sheets()
if not sheets:
    st.warning("No results yet. Run `python run_takeoff.py` (optionally `--all`) to generate outputs/.")
    st.stop()

labels = [s["sheet"] for s in sheets]
with st.sidebar:
    st.header("Sheets")
    pick = st.radio("Select a sheet", labels, index=0)
    st.caption("Generate more with `python run_takeoff.py --all`")
chosen = next(s for s in sheets if s["sheet"] == pick)
stem = chosen["stem"]

report = _load_report(stem)
if report is None:
    st.error(f"report_{stem}.json missing."); st.stop()

fpi = report.get("feet_per_inch")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Sheet", report.get("sheet", pick))
c2.metric("Scale", f'1" = {fpi:.0f}\'' if fpi else "—", help=f"{report['pixels_per_foot']} px/ft @ {report['dpi']} DPI")
c3.metric("Mean abs. error (MAPE)", f"{report['mape']:.1f}%")
rm = report.get("raster_mape")
c4.metric("Improvement", f"{rm:.0f}% → {report['mape']:.1f}%" if rm else "vector-exact",
          help="Raster color-seg baseline → exact vector polygons (L1.01)")

st.divider()
left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("Annotated sheet")
    img = OUT / f"annotated_{stem}.png"
    if img.exists():
        st.image(str(img), use_container_width=True,
                 caption="Detected area regions outlined + labeled (code · name · measured sq ft)")
    else:
        st.info(f"annotated_{stem}.png not found.")

with right:
    st.subheader("Measured vs ground truth")
    fills = report.get("fill_rgb", {})
    rcounts = report.get("region_count", {})
    flags = report.get("flags", {})
    rows = []
    for r in report["rows"]:
        rows.append({
            "Code": r["code"], "Material": r["name"],
            "Fill": _chip(fills.get(r["code"], [])),
            "Ground truth": r["ground_truth"], "Measured": r["measured"],
            "Error %": r["error_pct"], "Regions": rcounts.get(r["code"], 0),
            "Flag": "⚠︎" if flags.get(r["code"]) else "",
        })
    df = pd.DataFrame(rows)

    def color_err(v):
        a = abs(v)
        c = "#1a7f37" if a < 5 else ("#9a6700" if a < 15 else "#cf222e")
        return f"color: {c}; font-weight: 600"

    styled = (df.style
              .map(color_err, subset=["Error %"])
              .map(lambda v: f"background-color: {v}" if isinstance(v, str) and v.startswith("rgb") else "",
                   subset=["Fill"])
              .format({"Ground truth": "{:,.2f}", "Measured": "{:,.2f}", "Error %": "{:+.1f}%"})
              .format({"Fill": lambda v: ""}, subset=["Fill"]))
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption("Green < 5% · amber < 15% · red ≥ 15% error vs the human QTO. ⚠︎ = needs review (e.g. shared color).")

    if any(flags.get(r["code"]) for r in report["rows"]):
        st.markdown("**Flags**")
        for r in report["rows"]:
            f = flags.get(r["code"])
            if f:
                st.caption(f"• {r['code']} {r['name']}: {f}")

    chart = df.copy(); chart["abs_err"] = chart["Error %"].abs()
    st.markdown("**Per-item error**")
    st.bar_chart(chart.set_index("Code")["abs_err"], height=200)

st.divider()
with st.expander("Gemini validation & calibration notes"):
    for n in report.get("notes", []):
        st.text(n)
with st.expander("Full run log (RESULTS.md)"):
    rp = ROOT / "RESULTS.md"
    if rp.exists():
        st.markdown(rp.read_text(encoding="utf-8"))
