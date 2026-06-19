"""Per-plan data model (pydantic v2). The renderer consumes this; it never
hardcodes quantities. Geometry is in base-page PDF points (origin top-left,
y down). Totals are rendered verbatim — a string total (e.g. "46.6") preserves
source trailing-zero behavior.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from . import style
from .style import Geometry

Point = tuple[float, float]
Polyline = list[Point]
Polygon = list[Point]


class Base(BaseModel):
    """Reference to the base plan page the overlay is drawn on."""
    model_config = ConfigDict(extra="forbid")
    pdf: str
    page_index: int = 0
    width: float
    height: float
    rotation: int = 0


class ElementData(BaseModel):
    """Geometry + precomputed total for one taxonomy element. Which geometry
    field is populated depends on the element's locked geometry type."""
    model_config = ConfigDict(extra="forbid")
    total: float | int | str
    segments: list[Polyline] | None = None   # LINEAR
    polygons: list[Polygon] | None = None     # AREA
    points: list[Point] | None = None         # POINT
    origin: Point | None = None               # light_run convergence point


class DimLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    pos: Point
    angle: float = 0.0          # degrees, for parallel placement along an edge


class PlanData(BaseModel):
    """A full plan's measured data for one QTO sheet."""
    model_config = ConfigDict(extra="forbid")
    base: Base
    elements: dict[str, ElementData]
    dimension_labels: list[DimLabel] = []
    legend_origin: Point | None = None   # box top-left; auto-placed if omitted

    @model_validator(mode="after")
    def _validate_elements(self) -> "PlanData":
        for key, data in self.elements.items():
            el = style.BY_KEY.get(key)
            if el is None:
                raise ValueError(f"unknown element key: {key!r}")
            if el.geometry is Geometry.LINEAR and key != "light_run":
                if not data.segments:
                    raise ValueError(f"{key}: linear element needs `segments`")
            elif el.geometry is Geometry.AREA:
                if not data.polygons:
                    raise ValueError(f"{key}: area element needs `polygons`")
            elif el.geometry is Geometry.POINT:
                if data.points is None:
                    raise ValueError(f"{key}: point element needs `points`")
            if key == "light_run" and data.origin is None:
                raise ValueError("light_run needs an `origin`")
        return self

    def ordered(self) -> list[tuple[str, ElementData | None]]:
        """The 14 elements in fixed legend order; missing ones yield None."""
        return [(k, self.elements.get(k)) for k in style.ORDER]
