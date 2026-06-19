"""Central configuration for the L1.01 surface-area takeoff.

Everything tunable lives here so the iteration loop (s8) can sweep thresholds
without touching stage code.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --- paths -----------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# Load .env from the project dir (key never hardcoded).
load_dotenv(PROJECT_DIR / ".env")

# Input drawing. Overridable via env var for portability.
PDF_PATH = Path(os.environ.get("OE_PDF_PATH", r"C:\Users\absar\Downloads\2811 KIRBY QTO.pdf"))

# --- sheet ------------------------------------------------------------------
DEFAULT_SHEET = "L1.01"
DEFAULT_PAGE_INDEX = 0   # L1.01 is page 1 (0-based) of the QTO PDF
RENDER_DPI = 200         # render resolution for detection

# Back-compat aliases (page-0 defaults).
SHEET = DEFAULT_SHEET
PAGE_INDEX = DEFAULT_PAGE_INDEX

# --- scale / calibration ----------------------------------------------------
# The scale differs per sheet (e.g. L1.01 = 1"=10', L1.02/L1.03 = 1"=8'), so it
# is read per sheet (Gemini OCR of the title block) with this as a fallback.
# The PDF pages are true paper size (48"x36", ARCH-E), so for a given scale:
#   pixels_per_foot = RENDER_DPI / feet_per_inch
DEFAULT_FEET_PER_INCH = 10.0
FEET_PER_INCH = DEFAULT_FEET_PER_INCH   # back-compat alias
EXPECTED_PAGE_W_IN = 48.0   # used to sanity-check the true-paper-size assumption
EXPECTED_PAGE_H_IN = 36.0

# --- external ground truth (task brief Section 4, L1.01 only) ---------------
# The legend prints each item's QTO value, which IS the human takeoff and is
# used as the comparison baseline on every sheet. For L1.01 these match the
# task's Section 4 table exactly; we keep that table for an explicit cross-check.
TARGET_CODES = ["M.5", "M.6", "M.7", "M.15", "M.16"]   # L1.01 raster-baseline only
SECTION4_GROUND_TRUTH = {
    "M.5":  {"name": "Concrete Paver A", "sqft": 6550.15},
    "M.6":  {"name": "Concrete Paver B", "sqft": 1760.05},
    "M.7":  {"name": "Tile Paver A",     "sqft": 191.42},
    "M.15": {"name": "River Rock",       "sqft": 368.57},
    "M.16": {"name": "Beach Pebble",     "sqft": 413.5},
}
GROUND_TRUTH = SECTION4_GROUND_TRUTH    # back-compat alias

# L1.01 raster-fallback specifics (the deterministic vector path doesn't need these).
BLUE_FAMILY = ["M.6", "M.15"]
NOISY_CODES = ["M.7"]

# --- segmentation defaults (the iteration loop overrides these) -------------
# Per-channel RGB tolerance around the sampled swatch color. Larger = more
# fill captured but more bleed into neighbours / anti-aliased edges.
DEFAULT_RGB_TOL = 26
# Morphological close kernel (px) to fill hatching gaps inside a fill region.
DEFAULT_CLOSE_PX = 5
# Drop connected components smaller than this many sq ft (kills speckle/text).
DEFAULT_MIN_REGION_SQFT = 3.0

# --- gemini -----------------------------------------------------------------
GEMINI_MODEL = "gemini-2.5-flash"


def gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY")


def sqft_per_pixel(dpi: int = RENDER_DPI, feet_per_inch: float = DEFAULT_FEET_PER_INCH) -> float:
    """Area of one rendered pixel in real square feet, from the sheet scale."""
    ppf = dpi / feet_per_inch          # pixels per foot
    return (1.0 / ppf) ** 2


def sqft_per_point2(feet_per_inch: float = DEFAULT_FEET_PER_INCH) -> float:
    """Area of one PDF point^2 in real square feet (for the vector layer).

    1 point = 1/72 inch; 1 inch = feet_per_inch feet -> 1 pt = feet_per_inch/72 ft.
    """
    return (feet_per_inch / 72.0) ** 2


# Vector fills with these colors are not material areas (sheet background, etc.).
VECTOR_IGNORE_COLORS = {(255, 255, 255)}
# Tolerance (per channel) when matching a plan fill to a legend swatch's fill color.
VECTOR_COLOR_TOL = 8

# On-plan symbol/keynote markers are small, near-square fills stamped at an
# identical footprint many times. Material areas don't repeat at one exact size,
# so we drop fills that are small AND near-square AND share their footprint with
# >= MARKER_MIN_REPEAT same-color fills. This fixes M.7/M.15 without touching
# M.16's legitimate small pebble patches.
MARKER_MAX_SQFT = 12.0
MARKER_ASPECT_RANGE = (0.6, 1.7)
MARKER_MIN_REPEAT = 3
