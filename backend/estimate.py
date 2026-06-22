"""Combined project estimate in the Outdoor Elements scope-of-work format.

Aggregates the takeoff from ALL detected pages of a job into ONE estimate
structured like the OE proposal: discipline sections (Swimming Pool & Spa,
Hardscape, Landscape/Planting), Hardscape split into subsections (Pavers, Walls,
Gravel & Boulders, Artificial Turf, Site Furnishings, Outdoor Kitchen, Fire Pit,
Planters), section subtotals, and a grand total.

    from backend import estimate
    est = estimate.build_estimate(job_id)
    # {sections:[{name,total,subsections:[{name,total,lines}],lines:[...]}], grand_total}
"""
from __future__ import annotations

import re

from . import estimate_pricing, pricing, store

# discipline + (hardscape) subsection for a material, by code/description
_POOL = re.compile(r"\b(pool|spa|coping|tanning|aquatic|water\s*feature)", re.I)
_PLANT = re.compile(r"\b(tree|shrub|plant|grass|sod|ground\s*cover|annual|perennial|"
                    r"mulch|palm|vine|holly|oak|fern|sage|azalea|boxwood|liriope|nandina)", re.I)
_SUBS = [
    ("Walls", re.compile(r"\b(wall|veneer|screen)", re.I)),
    ("Gravel and Boulders", re.compile(r"\b(gravel|river\s*rock|rock|pebble|boulder|granite|\bdg\b|decomposed)", re.I)),
    ("Artificial Turf", re.compile(r"\b(artificial\s*turf|turf)", re.I)),
    ("Outdoor Kitchen", re.compile(r"\b(kitchen|grill|counter|bar\b)", re.I)),
    ("Fire Pit", re.compile(r"\b(fire\s*pit|firepit)", re.I)),
    ("Planters", re.compile(r"\b(planter)", re.I)),
    ("Site Furnishings", re.compile(r"\b(furnish|bench|bollard|bike\s*rack|tree\s*grate|trash|"
                                    r"receptacle|light|sign|fixture|umbrella|table|chair)", re.I)),
    ("Pavers", re.compile(r"\b(paver|concrete|tile|stone|pavement|sidewalk|patio|step|stair|"
                          r"deck|band|surface)", re.I)),
]


def categorize(code: str, name: str) -> tuple[str, str | None]:
    """Return (discipline, subsection|None) for a material."""
    t = f"{name or ''} {code or ''}"
    if _POOL.search(t):
        return ("SWIMMING POOL & SPA", None)
    if (code or "").upper().startswith("SF"):
        return ("HARDSCAPE", "Site Furnishings")
    for sub, rx in _SUBS:
        if rx.search(t):
            return ("HARDSCAPE", sub)
    if _PLANT.search(t):
        return ("LANDSCAPE / PLANTING", None)
    return ("HARDSCAPE", "Other")


def _job_rate_table(job_id: str) -> dict:
    src = store.estimate_path(job_id)
    if not src.exists():
        return {}
    try:
        return estimate_pricing.parse_estimate(str(src)).rate_table()
    except Exception:  # noqa: BLE001
        return {}


def _collect_areas(job_id: str) -> tuple[dict, dict]:
    """Sum area (sq ft) per material code across every detected page; + names.
    Plant codes (counted as species) are excluded — a tree mis-detected as a big
    'area' zone must not appear as hardscape SF in the estimate."""
    status = store.read_status(job_id) or {}
    n = int(status.get("page_count") or 0)
    cfg = store.read_config(job_id) or {}
    mats = cfg.get("materials") if isinstance(cfg.get("materials"), dict) else {}

    # codes that the planting pass counted as species -> not area materials
    plant_codes: set[str] = set()
    for p in range(n):
        s2 = store.read_stage2(job_id, p)
        for t in (s2 or {}).get("takeoff", []) or []:
            if t.get("source") == "planting" and t.get("code"):
                plant_codes.add(str(t["code"]).upper())

    areas: dict[str, float] = {}
    names: dict[str, str] = {}
    for p in range(n):
        s2 = store.read_stage2(job_id, p)
        if not s2 or not s2.get("groups"):
            continue
        for g in s2["groups"]:
            code, sqft = g.get("label"), g.get("sqft")
            if not code or not sqft or str(code).upper() in plant_codes:
                continue
            areas[code] = areas.get(code, 0.0) + float(sqft)
            if code not in names:
                info = mats.get(code)
                names[code] = (info.get("name") if isinstance(info, dict)
                               else info if isinstance(info, str) else "") or ""
    return areas, names


_SEC_ORDER = ["SWIMMING POOL & SPA", "HARDSCAPE", "LANDSCAPE / PLANTING"]
_SUB_ORDER = ["Pavers", "Walls", "Gravel and Boulders", "Artificial Turf",
              "Outdoor Kitchen", "Fire Pit", "Planters", "Site Furnishings", "Other"]


def build_estimate(job_id: str) -> dict:
    """Combined OE-format estimate across all detected pages of the job."""
    areas, names = _collect_areas(job_id)
    rates = store.read_prices(job_id) or {}
    est = _job_rate_table(job_id)
    changed = False
    for code in areas:
        if code not in rates:
            er = est.get(code)
            rates[code] = (er["rate"] if er and er.get("rate")
                           else pricing.default_rate(code, names.get(code, "")))
            changed = True
    if changed:
        store.write_prices(job_id, rates)

    sections: dict[str, dict] = {}
    grand = 0.0
    for code, qty in areas.items():
        rate = float(rates.get(code, pricing._DEFAULT_RATE))
        cost = round(qty * rate, 2)
        grand += cost
        row = {"code": code, "name": names.get(code, ""), "qty": round(qty, 1),
               "unit": "sq ft", "rate": round(rate, 2), "cost": cost}
        disc, sub = categorize(code, names.get(code, ""))
        sec = sections.setdefault(disc, {"subs": {}, "lines": []})
        if sub:
            sec["subs"].setdefault(sub, []).append(row)
        else:
            sec["lines"].append(row)

    out = []
    for name in _SEC_ORDER + [s for s in sections if s not in _SEC_ORDER]:
        if name not in sections:
            continue
        sec = sections[name]
        subs = []
        for sname in _SUB_ORDER + [s for s in sec["subs"] if s not in _SUB_ORDER]:
            rows = sec["subs"].get(sname)
            if not rows:
                continue
            rows = sorted(rows, key=lambda r: -r["cost"])
            subs.append({"name": sname, "total": round(sum(r["cost"] for r in rows), 2),
                         "lines": rows})
        lines = sorted(sec["lines"], key=lambda r: -r["cost"])
        total = round(sum(r["cost"] for r in lines) + sum(s["total"] for s in subs), 2)
        out.append({"name": name, "total": total, "subsections": subs, "lines": lines})

    return {"sections": out, "grand_total": round(grand, 2),
            "page_count": len([1 for p in range(int((store.read_status(job_id) or {}).get("page_count") or 0))
                               if (store.read_stage2(job_id, p) or {}).get("groups")])}
