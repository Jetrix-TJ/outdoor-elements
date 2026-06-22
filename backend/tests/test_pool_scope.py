"""Unit tests for pool_scope regex parser. No PDF needed — pass text directly."""

SAMPLE_SECTION = """\
SWIMMING POOL (1,109 SF)
1. Shop drawings, structural engineering, submittals, and Operation &
Maintenance manuals. Two (2) design and coordination meetings.
2. Provide and install Gunite 8" thick and reinforcement #4 - 10" o.c.e.w.
5. Provide and install equipment for the pool in equipment room located near body of
water:
a. 1 – Pentair Intelliflo 5HP VS + SVRS pump for body of water.
b. 1 – Pentair Clean and Clear Cartridge filter for body of water.
d. 12 – Globrite/Microbrite Lights per OE selection.
10. One-year warranty on installation and materials.
Swimming Pool Total: $549,863.52
"""

FULL_DOC = """\
LANDSCAPE
Some landscape content.
Landscape Total: $100,000.00

SWIMMING POOL (1,109 SF)
1. Shop drawings and coordination.
Swimming Pool Total: $549,863.52

HARDSCAPE
Some hardscape content.
"""


def test_find_section_returns_pool_block():
    from backend.pool_scope import _find_section
    result = _find_section(FULL_DOC, "SWIMMING POOL")
    assert result is not None
    assert "Shop drawings" in result
    assert "HARDSCAPE" not in result


def test_find_section_returns_none_when_missing():
    from backend.pool_scope import _find_section
    assert _find_section("no pool here", "SWIMMING POOL") is None


def test_parse_sf_from_header():
    from backend.pool_scope import _parse_sf
    assert _parse_sf("SWIMMING POOL (1,109 SF)") == 1109
    assert _parse_sf("SWIMMING POOL 1109 SF") == 1109
    assert _parse_sf("no sf here") is None


def test_parse_total():
    from backend.pool_scope import _parse_total
    assert _parse_total(SAMPLE_SECTION, "SWIMMING POOL") == 549863.52
    assert _parse_total("no total", "SWIMMING POOL") is None


def test_parse_items_numbered():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    numbers = [it["number"] for it in items]
    assert 1 in numbers
    assert 2 in numbers
    assert 10 in numbers


def test_parse_items_sub_items():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    item5 = next(it for it in items if it["number"] == 5)
    assert len(item5["sub_items"]) >= 3
    sub_a = next(s for s in item5["sub_items"] if s["label"] == "a")
    assert sub_a["qty"] == 1
    assert "Pentair" in sub_a["description"]
    sub_d = next(s for s in item5["sub_items"] if s["label"] == "d")
    assert sub_d["qty"] == 12


def test_parse_items_type_is_none_without_gemini():
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    assert all(it["type"] is None for it in items)


def test_parse_pool_scope_returns_none_for_missing_section():
    import tempfile, os
    from pathlib import Path
    import fitz
    from backend.pool_scope import parse_pool_scope
    # create a tiny PDF with no pool section
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), "LANDSCAPE\nSome content.\n")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    try:
        assert parse_pool_scope(tmp) is None
    finally:
        os.unlink(tmp)


def test_parse_items_no_total_in_last_item():
    """M1 regression: total line must not appear in any item's text."""
    from backend.pool_scope import _parse_items
    items = _parse_items(SAMPLE_SECTION)
    for it in items:
        assert "Total:" not in it["text"], (
            f"Item {it['number']} text contains 'Total:': {it['text']!r}"
        )
        assert "$" not in it["text"], (
            f"Item {it['number']} text contains '$': {it['text']!r}"
        )


def test_parse_pool_scope_full():
    import tempfile, os
    import fitz
    from backend.pool_scope import parse_pool_scope
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((50, 50), SAMPLE_SECTION)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    doc.save(tmp)
    doc.close()
    try:
        result = parse_pool_scope(tmp)  # no api_key → no Gemini
        assert result is not None
        assert result["scope_type"] == "SWIMMING POOL"
        assert result["area_sf"] == 1109
        assert result["total_price"] == 549863.52
        assert len(result["items"]) >= 3
    finally:
        os.unlink(tmp)
