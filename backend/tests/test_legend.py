"""The legend reader must auto-detect each project's takeoff material family
from the raw plan's callout tags (Kirby M.x, Pelican A#) — the dominant family,
ignoring plant/wall/note families. Self-contained synthetic PDF."""
import fitz

from backend import legend


def _plan(tmp_path, tokens):
    """A page sprinkled with code tokens at scattered positions."""
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    x, y = 40, 40
    for tok in tokens:
        page.insert_text((x, y), tok, fontsize=9)
        x += 70
        if x > 740:
            x = 40
            y += 30
    p = tmp_path / "plan.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_dominant_family_is_materials(tmp_path):
    # M dominates (materials); PL = plants, W = walls (should not win)
    toks = (["M.5"] * 6 + ["M.6"] * 4 + ["M.7"] * 2
            + ["PL.1"] * 5 + ["PL.2"] * 3 + ["W.1"] * 3)
    p = _plan(tmp_path, toks)
    fam = legend.detect_material_family(p, 0)
    assert fam["family"] == "M"
    assert fam["codes"] == ["M.5", "M.6", "M.7"]
    assert fam["all_families"]["M"] == 12


def test_generalizes_to_a_family(tmp_path):
    # Pelican-style: A is the material family, F/C are minor
    toks = ["A2"] * 8 + ["A7"] * 5 + ["A3"] * 4 + ["F1"] * 3 + ["C2"] * 2
    p = _plan(tmp_path, toks)
    cfg = legend.tag_config(p, 0)
    assert cfg["family"] == "A"
    assert cfg["tag_numeric_only"] is False
    # the pattern must match the full code (A2), not just digits
    import re
    rx = re.compile(cfg["tag_pattern"], re.I)
    assert rx.match("A2") and rx.match("A7")
    assert not rx.match("F1")


def test_none_when_no_tags(tmp_path):
    p = _plan(tmp_path, ["NORTH", "SCALE", "PLAN"])
    assert legend.detect_material_family(p, 0) is None
    assert legend.tag_config(p, 0) is None


def test_area_codes_multi_family_excludes_non_area(tmp_path):
    # Pavers (P) + tile (T) are area; site furnishings (SF) are count -> excluded.
    # Only P.1 and T.1 are spelled out in the legend; T.2 must ride in on T.1.
    toks = ["P.1", "P.2", "P.3", "T.1", "T.2", "SF.1", "SF.2"]
    p = _plan(tmp_path, toks)
    materials = {"P.1": "Pedestal Pavers - Type 1", "T.1": "Porcelain Deck Tile",
                 "SF.1": "Site Furnishing - Bench"}
    codes = legend.area_material_codes(p, 0, materials=materials)
    assert codes == ["P.1", "P.2", "P.3", "T.1", "T.2"]
    # the derived pattern matches the area families and rejects the SF family
    import re
    rx = re.compile(legend.area_tag_pattern(codes), re.I)
    assert rx.match("P.2") and rx.match("T.2")
    assert not rx.match("SF.1")


def test_area_codes_single_family_unchanged(tmp_path):
    # Kirby-style: only M (paving) is a real material; R (rail) is linear and must
    # not be pulled in even though it carries code-like callouts.
    toks = ["M.5"] * 3 + ["M.6"] * 2 + ["R.1", "R.2"]
    p = _plan(tmp_path, toks)
    materials = {"M.5": "Concrete Paver A", "M.6": "Concrete Paver B",
                 "R.1": "Metal Railing"}
    codes = legend.area_material_codes(p, 0, materials=materials)
    assert codes == ["M.5", "M.6"]


def test_area_codes_none_without_legend(tmp_path):
    # No materials dict -> cannot confirm any area family -> None (caller keeps
    # its existing no-filter behaviour).
    p = _plan(tmp_path, ["P.1", "P.2"])
    assert legend.area_material_codes(p, 0, materials=None) is None
