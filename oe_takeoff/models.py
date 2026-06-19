"""Typed outputs (Pydantic v2), mirroring Grodsky's typed-stage pattern."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LegendItem(BaseModel):
    """One legend row for an area material."""
    code: str
    name: str
    unit: str = "area_sqft"
    swatch_rgb: tuple[int, int, int]              # faded raster swatch color
    fill_rgb: Optional[tuple[int, int, int]] = None  # true vector fill color
    legend_value_sqft: Optional[float] = None     # value printed in the legend, if read


class Region(BaseModel):
    """A detected fill region (page-pixel coords)."""
    bbox: list[int]            # [x0, y0, x1, y1] in render pixels
    pixel_area: int
    sqft: float
    contour: Optional[list[list[int]]] = None   # polygon points (px) for vector regions


class TakeoffRow(BaseModel):
    """Final measured result for one material on the sheet."""
    sheet: str
    code: str
    name: str
    unit: str = "area_sqft"
    measured_sqft: float
    region_count: int = 0
    note: Optional[str] = None


class ComparisonRow(BaseModel):
    code: str
    name: str
    ground_truth: float
    measured: float
    error_pct: float           # signed: (measured - gt) / gt * 100


class Report(BaseModel):
    sheet: str
    rows: list[ComparisonRow]
    mape: float                # mean absolute % error across reported rows
    pixels_per_foot: float
    dpi: int
    notes: list[str] = Field(default_factory=list)
