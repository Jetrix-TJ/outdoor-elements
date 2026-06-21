"""The general QTO ground-truth parser must read any project's Legend — varied
code styles (M.5 / A2 / (F-101) / plain names) and units (area/linear/count).
Self-contained: builds a synthetic QTO PDF so it runs without the project data."""
import fitz

from backend import groundtruth as gt


def _make_qto(tmp_path, legend_lines):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    y = 50
    page.insert_text((40, y), "Legend", fontsize=12)
    y += 24
    for ln in legend_lines:
        page.insert_text((40, y), ln, fontsize=11)
        y += 20
    p = tmp_path / "qto.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_parses_area_linear_count_and_codes(tmp_path):
    p = _make_qto(tmp_path, [
        "A2 Gravel",
        "1,542.64 sq ft",          # area, code A2
        "A7 Concrete Border",
        "161.88 ft",               # linear, code A7
        "(F-101) 8'HT Screen Wall",
        "206.83 ft",               # linear, parenthetical code
        "Cabanas",
        "338.83 sq ft",            # area, plain name (no code)
        "(D) Cabana",
        "3",                       # count, bare integer
    ])
    items = gt.parse_qto(p)
    by_name = {it.name: it for it in items}

    assert by_name["A2 Gravel"].unit == "sqft"
    assert abs(by_name["A2 Gravel"].qty - 1542.64) < 0.01
    assert by_name["A2 Gravel"].code == "A2"

    assert by_name["A7 Concrete Border"].unit == "ft"
    assert by_name["A7 Concrete Border"].code == "A7"

    assert by_name["(F-101) 8'HT Screen Wall"].unit == "ft"
    assert by_name["(F-101) 8'HT Screen Wall"].code == "F-101"

    assert by_name["Cabanas"].unit == "sqft"
    assert by_name["Cabanas"].code is None

    assert by_name["(D) Cabana"].unit == "each"
    assert by_name["(D) Cabana"].qty == 3


def test_same_line_name_and_quantity(tmp_path):
    p = _make_qto(tmp_path, [
        "Perimeter Wall 127.19 ft",        # name + qty on one line
        "(N-34) Pickleball Court 3,242.74 sq ft",
    ])
    items = gt.parse_qto(p)
    by_name = {it.name: it for it in items}
    assert by_name["Perimeter Wall"].unit == "ft"
    assert abs(by_name["Perimeter Wall"].qty - 127.19) < 0.01
    assert by_name["(N-34) Pickleball Court"].unit == "sqft"
    assert by_name["(N-34) Pickleball Court"].code == "N-34"


def test_summarize_rolls_up_by_unit(tmp_path):
    p = _make_qto(tmp_path, ["A2 Gravel", "100 sq ft", "Wall", "50 ft", "Tree", "4"])
    s = gt.summarize(gt.parse_qto(p))
    assert s["sqft"]["count"] == 1 and abs(s["sqft"]["total"] - 100) < 0.01
    assert s["ft"]["count"] == 1
    assert s["each"]["count"] == 1
