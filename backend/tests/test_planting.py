"""Planting count helpers: text label-frequency + the JSON count parser.
The vision call needs a key/network, so those parts are tested deterministically."""
import fitz

from backend import planting


def _plan(tmp_path, tokens):
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    x, y = 50, 50
    for t in tokens:
        page.insert_text((x, y), t, fontsize=9)
        x += 70
        if x > 720:
            x, y = 50, y + 30
    p = tmp_path / "plan.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_text_label_counts_and_anchoring(tmp_path):
    p = _plan(tmp_path, ["WR", "WR", "WR", "BG", "BG", "P-FJ", "NOISE123", "the"])
    # no anchor: counts every code-like token
    c = planting.text_label_counts(p, 0)
    assert c["WR"] == 3 and c["BG"] == 2 and c["P-FJ"] == 1
    # anchored to a schedule list: only those codes
    c2 = planting.text_label_counts(p, 0, valid_codes={"WR", "BG"})
    assert set(c2) == {"WR", "BG"} and c2["WR"] == 3


def test_parse_counts_strips_and_sums():
    reply = """```json
    [{"code":"BG","count":180},{"code":"bg","count":19},
     {"code":"WR","count":18},{"code":"X","count":0}]
    ```"""
    c = planting._parse_counts(reply)
    assert c["BG"] == 199          # BG + bg merged (case-insensitive)
    assert c["WR"] == 18
    assert "X" not in c            # zero dropped
