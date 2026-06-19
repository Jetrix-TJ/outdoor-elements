"""Parse the human pricing estimate (the OE proposal PDF) and derive the unit
rates it implies, so the AI can price its OWN measured quantities the same way
and be compared dollar-for-dollar against the human estimate.

Human method (reverse-engineered from the OE proposal):
  - Work is grouped into SUBSECTIONS (Pavers, Walls, Gravel & Boulders, Turf,
    Site Furnishings, ...). Each subsection prints a LUMP-SUM price.
  - Each subsection lists line items: (code) qty unit description. Areas are SF
    (M.x), walls/curbs are LF (W.x), furnishings/planters are each/EA (SF.x/PL.x).
  - Subsections roll up: Hardscape/Landscape -> Level -> + Pool + Spa + General
    Conditions -> grand total.

Implied per-unit rate for a subsection:
    rate(unit) = subsection_total / sum(qty of that unit in the subsection)
A subsection that is a single unit (e.g. one paver in SF) yields an exact rate;
a mixed-material same-unit subsection yields a blended rate for that unit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz

# (CODE) ... approx. <qty> SF|LF   OR   approx. (<count>) <desc>
_CODE = r"\(([A-Z]{1,3}[.\-]?\d+[A-Za-z]?)\)"
_AREA_LINE = re.compile(_CODE + r".*?approx\.?\s*([\d,]+)\s*(SF|LF)\b", re.I)
_COUNT_LINE = re.compile(_CODE + r".*?approx\.?\s*\((\d+)\)", re.I)
# subsection header carrying an inline lump sum:  "Pavers $192,053.73"
_SUBSECTION = re.compile(r"^([A-Za-z][A-Za-z &/]+?)\s+\$?\s*([\d,]+\.\d{2})\s*$")
# section roll-up totals:  "Swimming Pool Total: $549,863.52"
_TOTAL = re.compile(r"^(.+?Total)\s*:?\s*\$?\s*([\d,]+\.\d{2})\s*$", re.I)
_GRAND = re.compile(r"\$\s*([\d,]+\.\d{2})\s*\)")  # "... DOLLARS ($3,623,546.71)."


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class LineItem:
    code: str
    qty: float
    unit: str            # SF | LF | EA
    desc: str = ""


@dataclass
class Subsection:
    name: str
    total: float
    lines: list[LineItem] = field(default_factory=list)

    def rate_for_unit(self, unit: str) -> float | None:
        """Blended $/unit = subsection total / qty of that unit. Only meaningful
        when the subsection is dominated by one unit; returns None if no such qty."""
        q = sum(li.qty for li in self.lines if li.unit == unit)
        return (self.total / q) if q > 0 else None

    def units(self) -> set[str]:
        return {li.unit for li in self.lines}


@dataclass
class Estimate:
    subsections: list[Subsection]
    section_totals: dict[str, float]      # "Level 1 Hardscape Total" -> $
    grand_total: float | None

    def rate_table(self) -> dict[str, dict]:
        """Per material code -> {rate, unit, subsection, human_qty}. Uses the
        blended subsection rate for the line's unit."""
        out: dict[str, dict] = {}
        for ss in self.subsections:
            for li in ss.lines:
                rate = ss.rate_for_unit(li.unit)
                out[li.code] = {
                    "rate": round(rate, 2) if rate is not None else None,
                    "unit": li.unit,
                    "subsection": ss.name,
                    "subsection_total": ss.total,
                    "human_qty": li.qty,
                    "mixed_units": len(ss.units()) > 1,
                }
        return out


def parse_estimate(pdf_path: str) -> Estimate:
    doc = fitz.open(pdf_path)
    text = "\n".join(p.get_text("text") for p in doc)
    doc.close()
    lines = [ln.strip() for ln in text.splitlines()]

    subsections: list[Subsection] = []
    section_totals: dict[str, float] = {}
    current: Subsection | None = None

    for ln in lines:
        if not ln:
            continue
        mt = _TOTAL.match(ln)
        if mt:                                   # a roll-up total line ends a subsection
            section_totals[re.sub(r"\s+", " ", mt.group(1)).strip()] = _num(mt.group(2))
            current = None
            continue
        ms = _SUBSECTION.match(ln)
        if ms and "Total" not in ln:
            current = Subsection(re.sub(r"\s+", " ", ms.group(1)).strip(), _num(ms.group(2)))
            subsections.append(current)
            continue
        if current is not None:
            ma = _AREA_LINE.search(ln)
            if ma:
                current.lines.append(LineItem(ma.group(1).replace("-", "."),
                                              _num(ma.group(2)), ma.group(3).upper()))
                continue
            mc = _COUNT_LINE.search(ln)
            if mc:
                current.lines.append(LineItem(mc.group(1).replace("-", "."),
                                              float(mc.group(2)), "EA"))

    gm = _GRAND.search(text)
    grand = _num(gm.group(1)) if gm else None
    return Estimate(subsections, section_totals, grand)


def price_ai(measured: dict[str, float], rates: dict[str, dict]) -> dict:
    """Price AI-measured quantities with the human-derived rates.

    measured: {code: quantity}. Returns per-code rows + AI subsection totals and
    an overall AI total for the priced (measured) materials only."""
    rows = []
    by_subsection: dict[str, dict] = {}
    for code, info in rates.items():
        if info["rate"] is None:
            continue
        ai_qty = measured.get(code)
        if ai_qty is None:
            continue
        ai_cost = round(ai_qty * info["rate"], 2)
        human_cost = round(info["human_qty"] * info["rate"], 2)
        rows.append({
            "code": code, "unit": info["unit"], "rate": info["rate"],
            "subsection": info["subsection"],
            "human_qty": info["human_qty"], "ai_qty": round(ai_qty, 1),
            "human_cost": human_cost, "ai_cost": ai_cost,
            "delta_cost": round(ai_cost - human_cost, 2),
            "delta_pct": (round((ai_cost - human_cost) / human_cost * 100, 1)
                          if human_cost else None),
        })
        b = by_subsection.setdefault(info["subsection"],
                                     {"subsection": info["subsection"],
                                      "human_total": info["subsection_total"],
                                      "ai_total": 0.0, "priced_codes": 0})
        b["ai_total"] += ai_cost
        b["priced_codes"] += 1
    rows.sort(key=lambda r: -r["human_cost"])
    for b in by_subsection.values():
        b["ai_total"] = round(b["ai_total"], 2)
    return {"rows": rows,
            "subsections": sorted(by_subsection.values(), key=lambda b: -b["human_total"]),
            "ai_total_priced": round(sum(r["ai_cost"] for r in rows), 2),
            "human_total_priced": round(sum(r["human_cost"] for r in rows), 2)}
