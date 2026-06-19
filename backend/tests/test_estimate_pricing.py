"""Parse the OE pricing estimate and derive rates; compare AI vs human dollars."""
from pathlib import Path

import pytest

from backend import estimate_pricing as ep

PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\(OE) 2811 Kirby 01.07.2026.pdf"
pytestmark = pytest.mark.skipif(not Path(PDF).exists(), reason="OE estimate PDF not present")


def test_grand_total_parsed():
    est = ep.parse_estimate(PDF)
    assert est.grand_total == 3623546.71


def test_section_rollup_totals():
    est = ep.parse_estimate(PDF)
    st = est.section_totals
    assert st["Swimming Pool Total"] == 549863.52
    assert st["Spa Total"] == 236694.89
    assert st["Level 1 Hardscape Total"] == 731495.47
    assert st["Level 7 Hardscape Total"] == 1016050.52


def test_derived_sf_rates_for_pavers_and_turf():
    est = ep.parse_estimate(PDF)
    rates = est.rate_table()
    # Level 1 Pavers: (192,053.73) / (6550+1760+192) SF = 22.59/SF
    assert abs(rates["M.5"]["rate"] - 22.59) < 0.02
    assert rates["M.5"]["unit"] == "SF"
    # Level 38 Pavers: single material M.9 -> exact 163,745.53 / 3,937 = 41.59
    assert abs(rates["M.9"]["rate"] - 41.59) < 0.02
    # Artificial Turf M.12/M.13 blended
    assert abs(rates["M.12"]["rate"] - 71.87) < 0.05


def test_derived_rate_reproduces_subsection_total():
    """Sum of human line costs at the derived rate ~= the subsection lump sum."""
    est = ep.parse_estimate(PDF)
    pavers = next(s for s in est.subsections if s.name == "Pavers")  # Level 1 Pavers (first)
    rate = pavers.rate_for_unit("SF")
    recomputed = sum(li.qty * rate for li in pavers.lines)
    assert abs(recomputed - pavers.total) / pavers.total < 0.001


def test_price_ai_flags_quantity_discrepancy_in_dollars():
    est = ep.parse_estimate(PDF)
    rates = est.rate_table()
    # AI measured M.6 over-claims (4936 vs human 1760) -> AI cost much higher
    out = ep.price_ai({"M.5": 6402, "M.6": 4936, "M.7": 190}, rates)
    by = {r["code"]: r for r in out["rows"]}
    assert by["M.5"]["delta_pct"] < 0 and abs(by["M.5"]["delta_pct"]) < 5     # close
    assert by["M.6"]["delta_pct"] > 100                                       # over-claim flagged
    assert out["ai_total_priced"] > out["human_total_priced"]
