"""Pricing: turn measured takeoff quantities into a costed estimate.

cost(code) = quantity(code) x unit_rate(code).  Rates are $/sq ft for area
materials (the only thing the engine measures today). Rates live per-job in
prices.json so the estimator can tune them; we seed sensible starter rates by
material family from the L0.00 description, falling back to a flat default.
"""
from __future__ import annotations

# Starter $/sq ft rates keyed by a keyword in the material description.
# These are editable per job; they are only defaults to get a number on screen.
_RATE_BY_KEYWORD = [
    ("TILE PAVER", 32.0),
    ("CONCRETE PAVER", 18.0),
    ("PAVER", 20.0),
    ("WOOD DECK", 45.0),
    ("RIVER ROCK", 9.0),
    ("BEACH PEBBLE", 11.0),
    ("ARTIFICIAL TURF", 14.0),
    ("TURF", 14.0),
    ("DECOMPOSED GRANITE", 6.0),
    ("STONE", 28.0),
]
_DEFAULT_RATE = 15.0


def default_rate(code: str, desc: str = "") -> float:
    u = (desc or "").upper()
    for kw, rate in _RATE_BY_KEYWORD:
        if kw in u:
            return rate
    return _DEFAULT_RATE


def price_takeoff(areas: dict, rates: dict, names: dict | None = None) -> dict:
    """Build the costed table from {code: sqft} and {code: $/sqft}.

    Returns {rows: [{code,name,qty,unit,rate,cost}], total}."""
    names = names or {}
    rows = []
    total = 0.0
    for code, qty in sorted(areas.items(), key=lambda kv: -(kv[1] or 0)):
        rate = float(rates.get(code, _DEFAULT_RATE))
        cost = round(float(qty or 0) * rate, 2)
        total += cost
        rows.append({"code": code, "name": names.get(code, ""),
                     "qty": round(float(qty or 0), 1), "unit": "sq ft",
                     "rate": round(rate, 2), "cost": cost})
    return {"rows": rows, "total": round(total, 2)}
