"""General ground-truth parser for human QTO / Takeoff PDFs.

A QTO/Takeoff PDF is the *answer key*: the drawing marked up with measured regions
plus a printed **Legend** that lists each item and its measured quantity. This
module reads that Legend from ANY project, regardless of how the items are coded
(`M.5`, `A2 Gravel`, `(F-101) ...`, or a plain name like `Cabanas`) and regardless
of the quantity type:

    area   -> "1,542.64 sq ft"   (unit "sqft")
    linear -> "161.88 ft"        (unit "ft")
    count  -> "3"                (unit "each", a bare number)

It is used only while building, to score our raw->QTO detector against the human.
It is NOT part of the runtime path (in production there is no QTO).

    from backend import groundtruth
    items = groundtruth.parse_qto(r"...GRANBURY QTO.pdf")
    # -> [GTItem(code="A2", name="A2 Gravel", qty=1542.64, unit="sqft", page=0), ...]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

import fitz


@dataclass
class GTItem:
    name: str          # full legend label, e.g. "A2 Gravel" / "(F-101) Screen Wall"
    code: str | None   # leading code if any, e.g. "A2", "F-101", "M.5"
    qty: float         # measured quantity
    unit: str          # "sqft" | "ft" | "each"
    page: int

    def as_dict(self) -> dict:
        return asdict(self)


# A trailing quantity: number (with thousands commas / decimals) + optional unit.
_AREA = r"(?:sq\.?\s*ft\.?|sf|s\.f\.|square\s+feet)"
_LIN = r"(?:lin\.?\s*ft\.?|lf|l\.f\.|ft\.?|feet|')"
_NUM = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"
_QTY_TAIL = re.compile(rf"^(?P<name>.*?)[\s:]*?(?P<num>{_NUM})\s*(?P<unit>{_AREA}|{_LIN})?\s*$",
                       re.I)
# A leading code: M.5 / M-5 / A2 / A7 / F-101 / (F-101) / (A) etc.
_CODE = re.compile(r"^\(?\s*([A-Z]{1,3}[-.\s]?\d{1,3}|[A-Z]\d{0,2})\s*\)?\b", re.I)


def _norm_unit(raw: str | None, num_text: str) -> str:
    if raw:
        r = raw.lower().replace(".", "").replace(" ", "")
        if r in ("sqft", "sf", "squarefeet"):
            return "sqft"
        return "ft"          # lf / ft / feet / '
    # no unit -> a bare number is a COUNT (e.g. "(D) Cabana" -> 3)
    return "each"


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" :-–—")


def _code_of(name: str) -> str | None:
    m = _CODE.match(name)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1))


def _looks_like_unit_word(line: str) -> bool:
    return bool(re.fullmatch(rf"(?:{_AREA}|{_LIN})", line.strip(), re.I))


def parse_legend_lines(lines: list[str], page: int) -> list[GTItem]:
    """Turn one Legend block's lines into items. Handles the three shapes:
       name + qty on one line; name then qty on the next line; and the QTO's
       habit of repeating each printed line up to 3x."""
    # collapse consecutive duplicates (QTO renders fill/stroke/label = 3x)
    dedup: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if dedup and dedup[-1] == s:
            continue
        dedup.append(s)

    items: list[GTItem] = []
    name_buf: list[str] = []
    i = 0
    while i < len(dedup):
        line = dedup[i]
        if line.lower() == "legend":
            i += 1
            continue
        m = _QTY_TAIL.match(line)
        # a pure quantity line ("1,542.64 sq ft" or bare "3"): pair with buffer
        if m and not m.group("name").strip():
            name = _clean_name(" ".join(name_buf))
            name_buf = []
            if name:
                num = float(m.group("num").replace(",", ""))
                items.append(GTItem(name, _code_of(name), num,
                                    _norm_unit(m.group("unit"), m.group("num")), page))
            i += 1
            continue
        # name + trailing quantity on the SAME line
        if m and m.group("name").strip() and (m.group("unit") or _is_count_line(line)):
            name = _clean_name((" ".join(name_buf) + " " + m.group("name")).strip())
            name_buf = []
            num = float(m.group("num").replace(",", ""))
            items.append(GTItem(name, _code_of(name), num,
                                _norm_unit(m.group("unit"), m.group("num")), page))
            i += 1
            continue
        # otherwise it's (part of) a name
        name_buf.append(line)
        i += 1
    return items


def _is_count_line(line: str) -> bool:
    # "(D) Cabana 3" style — name then a small bare integer count
    m = re.search(r"\b(\d{1,3})\s*$", line)
    if not m:
        return False
    # avoid treating a measurement remainder as a count: only if no unit follows
    return True


def parse_qto(path: str) -> list[GTItem]:
    """Parse every page's Legend in a QTO/Takeoff PDF into ground-truth items."""
    out: list[GTItem] = []
    doc = fitz.open(path)
    try:
        for pno in range(len(doc)):
            text = doc[pno].get_text()
            if "legend" not in text.lower():
                continue
            lines = text.splitlines()
            # take from the FIRST "Legend" marker to the end of the page text
            start = next((k for k, l in enumerate(lines)
                          if l.strip().lower() == "legend"), None)
            if start is None:
                continue
            out.extend(parse_legend_lines(lines[start:], pno))
    finally:
        doc.close()
    return out


def summarize(items: list[GTItem]) -> dict:
    """Roll up by unit for a quick sanity view."""
    by_unit: dict[str, dict] = {}
    for it in items:
        b = by_unit.setdefault(it.unit, {"count": 0, "total": 0.0})
        b["count"] += 1
        b["total"] += it.qty
    return by_unit
