"""The fixed visual style contract for Pool & Spa QTO sheets.

Plan-AGNOSTIC. Every value here was sampled from the reference vector source
(AQ0.0, page 3 of `2811 KIRBY QTO.pdf`) and is locked. Nothing in this module
depends on a particular plan's quantities — only the per-plan `model` carries
those. Do not invent categories or reassign colors.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Geometry(str, Enum):
    LINEAR = "linear"   # colored stroke following an edge; unit = ft
    AREA = "area"       # semi-transparent fill over a polygon; unit = sq ft
    POINT = "point"     # filled circle at each instance; unit = count


class Unit(str, Enum):
    FT = "ft"
    SQ_FT = "sq ft"
    COUNT = "count"


@dataclass(frozen=True)
class Element:
    """One legend/taxonomy category. `key` is the stable id used in the data
    model; `name` is the human label shown in the legend."""
    key: str
    name: str
    geometry: Geometry
    color: str          # hex "#RRGGBB", the saturated source color
    unit: Unit
    opacity: float = 1.0


# The master schema: exactly 14 categories, in the FIXED legend order. Colors,
# geometry types, units and opacities are locked from the AQ0.0 vector source.
ELEMENTS: tuple[Element, ...] = (
    Element("coping",         "Coping",         Geometry.LINEAR, "#FF9800", Unit.FT),
    Element("total_sf",       "Total SF",       Geometry.AREA,   "#FF00DB", Unit.SQ_FT, 0.302),
    Element("tanning_ledge",  "Tanning Ledge",  Geometry.AREA,   "#009688", Unit.SQ_FT, 0.302),
    Element("waterline",      "Waterline",      Geometry.LINEAR, "#F0FF00", Unit.FT),
    Element("bench",          "Bench",          Geometry.LINEAR, "#00ECFF", Unit.FT),
    Element("steps",          "Steps",          Geometry.LINEAR, "#CDDC39", Unit.FT),
    Element("toe_tile",       "Toe Tile",       Geometry.LINEAR, "#3F51B5", Unit.FT),
    Element("planter",        "Planter",        Geometry.LINEAR, "#FF00DB", Unit.FT),
    Element("stone_steppers", "Stone Steppers", Geometry.AREA,   "#FFEB3B", Unit.SQ_FT, 0.302),
    Element("lights",         "Lights",         Geometry.POINT,  "#000000", Unit.COUNT),
    Element("drain_line",     "Drain line",     Geometry.LINEAR, "#FF00DB", Unit.FT),
    Element("light_run",      "Light run",      Geometry.LINEAR, "#E91E63", Unit.FT),
    Element("d_markers",      "D Markers",      Geometry.POINT,  "#03A9F4", Unit.COUNT),
    Element("skimmers",       "Skimmers",       Geometry.POINT,  "#00FFA0", Unit.COUNT),
)

# Fast lookup by key, preserving order semantics.
BY_KEY: dict[str, Element] = {e.key: e for e in ELEMENTS}
ORDER: tuple[str, ...] = tuple(e.key for e in ELEMENTS)


# ── geometry / stroke constants (PDF points) ────────────────────────────────
LINEAR_STROKE_WIDTH = 4.0       # all colored overlay lines
POINT_RADIUS = 9.6              # 19.2 pt diameter dots
LIGHT_RUN_WIDTH = 4.0           # crimson leaders

# ── dimension labels ────────────────────────────────────────────────────────
DIM_COLOR = "#21D022"
DIM_FONT = "Verdana"
DIM_FONT_SIZE = 14.0
DIM_LEADER_WIDTH = 3.0
DIM_DOT_RADIUS = 5.0

# ── legend layout (PDF points) ──────────────────────────────────────────────
LEGEND_FONT = "Times New Roman"
LEGEND_FONT_FILE = "times"      # fitz builtin serif alias ("times")
LEGEND_HEADER_SIZE = 18.14
LEGEND_ROW_SIZE = 18.14
LEGEND_BORDER = "#1A1A1A"
LEGEND_BORDER_WIDTH = 2.59
LEGEND_BG = "#FFFFFF"
LEGEND_CORNER_RADIUS = 14.0
LEGEND_WIDTH = 276.0
LEGEND_ROW_PITCH = 32.4
LEGEND_PAD_X = 16.0             # inner left padding to the symbol
LEGEND_HEADER_BASELINE = 31.0  # box top -> "Legend" header text baseline
LEGEND_FIRST_ROW_BASELINE = 62.0  # box top -> first row text baseline
LEGEND_BOTTOM_PAD = 22.0       # last row baseline -> box bottom
LEGEND_SYMBOL_W = 18.1         # swatch cell width
LEGEND_SYMBOL_LINE_WIDTH = 3.89
LEGEND_NAME_GAP = 10.0         # symbol cell to name
LEGEND_VALUE_PAD_R = 18.0      # right padding for right-aligned value
LEGEND_TEXT_MID = 6.0          # baseline -> vertical center of symbol
LEGEND_GAP_FROM_PLAN = 60.0    # auto-placement gap right of overlay bbox

# ── base underlay ───────────────────────────────────────────────────────────
BASE_DPI = 200                 # raster DPI for the embedded gray base
BASE_GRAY_LIGHTEN = 0.62       # blend toward white: 0 = original, 1 = white
