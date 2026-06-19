"""Number & unit formatting for the legend (strict rules from the spec, §6).

- Measured values: 2 decimals, comma thousands.
- Trailing-zero behavior matches the source: a value supplied as a string is
  kept verbatim (so `46.6` stays `46.6`); a numeric value is formatted to 2
  decimals with comma thousands.
- Unit suffix after one space: `ft` / `sq ft`.
- Counts: bare integer, no unit.
"""
from __future__ import annotations

from .style import Element, Geometry, Unit


def format_number(value: float | int | str) -> str:
    """Format a measured numeric value: comma thousands, 2 decimals. A string
    value is returned with comma thousands applied but its decimals untouched
    (preserves source trailing-zero behavior, e.g. '46.6')."""
    if isinstance(value, str):
        s = value.strip()
        if "." in s:
            int_part, dec_part = s.split(".", 1)
        else:
            int_part, dec_part = s, ""
        neg = int_part.startswith("-")
        digits = int_part.lstrip("-")
        grouped = f"{int(digits):,}" if digits.isdigit() else digits
        out = ("-" if neg else "") + grouped
        return f"{out}.{dec_part}" if dec_part != "" else out
    return f"{value:,.2f}"


def format_count(value: int | str) -> str:
    """Counts: bare integer, no unit, no decimals."""
    return str(int(value))


def format_value(element: Element, value) -> str:
    """Full legend value string for an element: number + unit suffix, or a bare
    integer for counts."""
    if element.geometry is Geometry.POINT:
        return format_count(value)
    num = format_number(value)
    return f"{num} {element.unit.value}"
