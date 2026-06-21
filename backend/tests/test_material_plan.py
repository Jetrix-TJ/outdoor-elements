"""The per-material brain classifies each takeoff material's quantity unit and
detect method. The Gemini vision path needs a network/key, so here we test the
deterministic keyword fallback (classify_material) and the JSON-reply parser."""
from backend import material_plan as mp


def test_keyword_classification_covers_all_three_units():
    # area (closed)
    assert mp.classify_material("Concrete Paver A") == {"unit": "area", "detect": "closed_area"}
    assert mp.classify_material("Artificial Turf") == {"unit": "area", "detect": "closed_area"}
    # area (open hatch) — the over-grabbing materials
    assert mp.classify_material("Blackstar Gravel")["detect"] == "open_hatch"
    assert mp.classify_material("Decomposed Granite")["detect"] == "open_hatch"
    assert mp.classify_material("River Rock")["detect"] == "open_hatch"
    # linear
    assert mp.classify_material("8'HT Perimeter Screen Wall")["unit"] == "linear"
    assert mp.classify_material("Concrete Border")["unit"] == "linear"
    assert mp.classify_material("Gravel Edging")["unit"] == "linear"   # edging wins over gravel
    # count
    assert mp.classify_material("Landscape Boulders")["unit"] == "count"
    assert mp.classify_material("Bench")["unit"] == "count"
    assert mp.classify_material("Fire Pit")["unit"] == "count"


def test_parse_json_reply_strips_fences_and_validates():
    reply = """```json
    [
      {"code":"M.5","name":"Concrete Paver","unit":"area","detect":"closed_area"},
      {"code":"M.15","name":"River Rock","unit":"area","detect":"open_hatch"},
      {"code":"W.1","name":"Seat Wall","unit":"linear","detect":"line"},
      {"code":"X","name":"junk","unit":"bogus","detect":"bogus"}
    ]
    ```"""
    items = mp._parse_json_list(reply)
    by = {i["code"]: i for i in items}
    assert by["M.15"]["detect"] == "open_hatch"
    assert by["W.1"]["unit"] == "linear"
    # invalid unit/detect repaired to area/closed_area defaults
    assert by["X"]["unit"] == "area" and by["X"]["detect"] == "closed_area"


def test_drops_non_takeoff_callouts():
    # layout/control points & survey marks must NOT become takeoff items
    assert not mp.is_takeoff_material("Construction Point")
    assert not mp.is_takeoff_material("LP Control Point")
    assert not mp.is_takeoff_material("Datum")
    assert mp.is_takeoff_material("Concrete Paver")
    reply = """[
      {"code":"LP1","name":"Construction Point","unit":"count","detect":"symbol"},
      {"code":"M.5","name":"Concrete Paver","unit":"area","detect":"closed_area"}
    ]"""
    items = mp._parse_json_list(reply)
    codes = {i["code"] for i in items}
    assert codes == {"M.5"}            # LP1 construction point dropped


def test_summarize_counts_by_unit():
    items = [{"unit": "area"}, {"unit": "area"}, {"unit": "linear"}, {"unit": "count"}]
    assert mp.summarize(items) == {"area": 2, "linear": 1, "count": 1}
