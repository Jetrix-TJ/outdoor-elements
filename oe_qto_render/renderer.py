"""The deterministic overlay renderer.

Given a `PlanData` (geometry + precomputed totals) and the locked `style`, draw
the QTO overlay + legend on the gray base, bottom -> top in the spec's stacking
order. Drives a `Canvas`, so SVG and PDF come from one draw path. No quantity is
computed or hardcoded here — totals come from the data; the light-run fan is
derived from the light points + origin.
"""
from __future__ import annotations

from . import style
from .base import render_gray_base
from .canvas import Canvas, PdfCanvas, SvgCanvas
from .format import format_value
from .model import ElementData, PlanData
from .style import Geometry

# layer order (bottom -> top) within each band
_AREA_ORDER = ("total_sf", "tanning_ledge", "stone_steppers")  # total_sf lowest
_LINEAR_NONCOPING = ("waterline", "bench", "steps", "toe_tile", "planter", "drain_line")
_POINT_ORDER = ("lights", "d_markers", "skimmers")


def _draw_area(c: Canvas, key: str, data: ElementData) -> None:
    el = style.BY_KEY[key]
    for poly in data.polygons or []:
        c.polygon(poly, fill=el.color, opacity=el.opacity)


def _draw_linear(c: Canvas, key: str, data: ElementData) -> None:
    el = style.BY_KEY[key]
    for seg in data.segments or []:
        c.polyline(seg, stroke=el.color, width=style.LINEAR_STROKE_WIDTH)


def _draw_light_run(c: Canvas, plan: PlanData) -> None:
    """One crimson leader from the single origin to each light dot (the fan)."""
    lr = plan.elements.get("light_run")
    lights = plan.elements.get("lights")
    if lr is None or lr.origin is None or lights is None:
        return
    el = style.BY_KEY["light_run"]
    for pt in lights.points or []:
        c.line(lr.origin, pt, stroke=el.color, width=style.LIGHT_RUN_WIDTH)


def _draw_points(c: Canvas, key: str, data: ElementData) -> None:
    el = style.BY_KEY[key]
    for (x, y) in data.points or []:
        c.circle(x, y, style.POINT_RADIUS, fill=el.color)


def _draw_dim_labels(c: Canvas, plan: PlanData) -> None:
    for lab in plan.dimension_labels:
        c.text(lab.pos[0], lab.pos[1], lab.text, style.DIM_FONT_SIZE,
               style.DIM_COLOR, style.DIM_FONT, anchor="start", angle=lab.angle)


# ── legend ──────────────────────────────────────────────────────────────────
def _overlay_bbox(plan: PlanData):
    xs: list[float] = []
    ys: list[float] = []

    def add(pts):
        for x, y in pts:
            xs.append(x); ys.append(y)

    for data in plan.elements.values():
        for seg in data.segments or []:
            add(seg)
        for poly in data.polygons or []:
            add(poly)
        if data.points:
            add(data.points)
        if data.origin:
            add([data.origin])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _legend_height() -> float:
    return (style.LEGEND_FIRST_ROW_BASELINE + 13 * style.LEGEND_ROW_PITCH
            + style.LEGEND_BOTTOM_PAD)


def _legend_origin(plan: PlanData) -> tuple[float, float]:
    if plan.legend_origin is not None:
        return plan.legend_origin
    bbox = _overlay_bbox(plan)
    h = _legend_height()
    if bbox is None:
        return plan.base.width * 0.7, plan.base.height * 0.4
    x0, y0, x1, y1 = bbox
    ox = min(x1 + style.LEGEND_GAP_FROM_PLAN,
             plan.base.width - style.LEGEND_WIDTH - 10)
    oy = (y0 + y1) / 2 - h / 2
    return ox, oy


def _draw_legend_symbol(c: Canvas, key: str, cell_x: float, cy: float) -> None:
    el = style.BY_KEY[key]
    w = style.LEGEND_SYMBOL_W
    if el.geometry is Geometry.LINEAR:
        c.line((cell_x, cy + w / 2), (cell_x + w, cy - w / 2),
               stroke=el.color, width=style.LEGEND_SYMBOL_LINE_WIDTH)
    elif el.geometry is Geometry.AREA:
        # filled square swatch at the element's true overlay opacity
        c.polygon([(cell_x, cy - w / 2), (cell_x + w, cy - w / 2),
                   (cell_x + w, cy + w / 2), (cell_x, cy + w / 2)],
                  fill=el.color, opacity=el.opacity)
    else:  # POINT
        c.circle(cell_x + w / 2, cy, w / 2 - 0.5, fill=el.color)


def _draw_legend(c: Canvas, plan: PlanData) -> None:
    x0, y0 = _legend_origin(plan)
    width = style.LEGEND_WIDTH
    height = _legend_height()
    c.rounded_rect(x0, y0, width, height, style.LEGEND_CORNER_RADIUS,
                   fill=style.LEGEND_BG, stroke=style.LEGEND_BORDER,
                   stroke_width=style.LEGEND_BORDER_WIDTH)
    # header
    c.text(x0 + width / 2, y0 + style.LEGEND_HEADER_BASELINE, "Legend",
           style.LEGEND_HEADER_SIZE, "#000000", style.LEGEND_FONT, anchor="middle")
    cell_x = x0 + style.LEGEND_PAD_X
    name_x = cell_x + style.LEGEND_SYMBOL_W + style.LEGEND_NAME_GAP
    value_x = x0 + width - style.LEGEND_VALUE_PAD_R
    for i, el in enumerate(style.ELEMENTS):
        baseline = y0 + style.LEGEND_FIRST_ROW_BASELINE + i * style.LEGEND_ROW_PITCH
        cy = baseline - style.LEGEND_TEXT_MID
        _draw_legend_symbol(c, el.key, cell_x, cy)
        c.text(name_x, baseline, el.name, style.LEGEND_ROW_SIZE, "#000000",
               style.LEGEND_FONT, anchor="start")
        data = plan.elements.get(el.key)
        total = data.total if data is not None else 0
        c.text(value_x, baseline, format_value(el, total), style.LEGEND_ROW_SIZE,
               "#000000", style.LEGEND_FONT, anchor="end")


# ── orchestration ───────────────────────────────────────────────────────────
def draw(c: Canvas, plan: PlanData, base_png: bytes | None,
         px_w: int | None, px_h: int | None) -> None:
    """Draw the full sheet onto `c` in stacking order."""
    if base_png is not None:
        c.image(base_png, 0, 0, plan.base.width, plan.base.height)
    # area fills: Total SF first (lowest), then the others
    for key in _AREA_ORDER:
        d = plan.elements.get(key)
        if d:
            _draw_area(c, key, d)
    # linear strokes (non-coping), then coping outline on top
    for key in _LINEAR_NONCOPING:
        d = plan.elements.get(key)
        if d:
            _draw_linear(c, key, d)
    if "coping" in plan.elements:
        _draw_linear(c, "coping", plan.elements["coping"])
    # light-run leaders, then point dots on top
    _draw_light_run(c, plan)
    for key in _POINT_ORDER:
        d = plan.elements.get(key)
        if d:
            _draw_points(c, key, d)
    # dimension labels, then legend
    _draw_dim_labels(c, plan)
    _draw_legend(c, plan)


def render_svg(plan: PlanData, *, with_base: bool = True) -> str:
    base_png = px_w = px_h = None
    if with_base:
        base_png, px_w, px_h = render_gray_base(plan.base)
    c = SvgCanvas(plan.base.width, plan.base.height)
    draw(c, plan, base_png, px_w, px_h)
    return c.tostring()


def render_pdf(plan: PlanData, out_path: str, *, with_base: bool = True) -> None:
    base_png = px_w = px_h = None
    if with_base:
        base_png, px_w, px_h = render_gray_base(plan.base)
    c = PdfCanvas(plan.base.width, plan.base.height)
    draw(c, plan, base_png, px_w, px_h)
    c.save(out_path)
