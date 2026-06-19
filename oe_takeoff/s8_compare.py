"""Stage 8 - compare measured areas to ground truth + report (adapts Grodsky s8).

Ground truth = the value printed in each legend row (the human QTO), which is
available on every sheet. For L1.01 these equal the task brief's Section 4 table.
"""
from __future__ import annotations

from . import config
from .models import ComparisonRow, LegendItem, Report, TakeoffRow


def build_rows(seg: dict, items: list[LegendItem], sheet: str) -> list[TakeoffRow]:
    rows: list[TakeoffRow] = []
    for it in items:
        info = seg.get(it.code) or {}
        rows.append(TakeoffRow(
            sheet=sheet, code=it.code, name=it.name,
            measured_sqft=info.get("sqft", 0.0),
            region_count=len(info.get("regions", [])),
            note=info.get("note"),
        ))
    return rows


def compare(rows: list[TakeoffRow], items: list[LegendItem], calib: dict,
            sheet: str, notes: list[str]) -> Report:
    gt_by_code = {it.code: (it.legend_value_sqft or 0.0) for it in items}
    crows: list[ComparisonRow] = []
    for r in rows:
        gt = gt_by_code.get(r.code, 0.0)
        err = (r.measured_sqft - gt) / gt * 100.0 if gt else 0.0
        crows.append(ComparisonRow(code=r.code, name=r.name, ground_truth=gt,
                                   measured=r.measured_sqft, error_pct=round(err, 1)))
    mape = sum(abs(c.error_pct) for c in crows) / len(crows) if crows else 0.0
    return Report(sheet=sheet, rows=crows, mape=round(mape, 1),
                  pixels_per_foot=round(calib["pixels_per_foot"], 2),
                  dpi=config.RENDER_DPI, notes=notes)


def format_table(report: Report) -> str:
    lines = [
        f"\n=== {report.sheet} surface-area takeoff vs ground truth "
        f"(ppf={report.pixels_per_foot}, {report.dpi} DPI) ===",
        f"{'code':<6}{'name':<22}{'ground_truth':>14}{'measured':>12}{'error_%':>10}",
        "-" * 64,
    ]
    for c in report.rows:
        lines.append(f"{c.code:<6}{c.name[:21]:<22}{c.ground_truth:>14,.2f}"
                     f"{c.measured:>12,.2f}{c.error_pct:>9.1f}%")
    lines.append("-" * 64)
    lines.append(f"Mean absolute % error (MAPE): {report.mape:.1f}%")
    return "\n".join(lines)
