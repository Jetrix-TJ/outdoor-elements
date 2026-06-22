"""Tests for backend.zone_filter — heuristic + Gemini false-positive filter."""
import pytest
from backend.zone_filter import filter_false_positives


def _zone(id_, area, bbox):
    return {
        "id": id_,
        "code": "M.5",
        "hex": "#ff0",
        "area_sqft": area,
        "perimeter_lf": 10.0,
        "geometry": [],
        "bbox": bbox,
        "source": "engine",
        "status": "active",
    }


def test_legend_strip_auto_drop():
    """Zones in right 25% (x0 > 0.75) are dropped without Gemini."""
    zones = [_zone("a", 200.0, [0.80, 0.1, 0.95, 0.3])]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    assert result == []


def test_sub_noise_auto_drop():
    """Zones under AUTO_DROP_SQFT (3 sqft) are dropped."""
    zones = [_zone("b", 2.0, [0.1, 0.1, 0.2, 0.2])]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    assert result == []


def test_large_drawing_zone_auto_keep():
    """Zones >= AUTO_KEEP_SQFT (50 sqft) in drawing area are kept without Gemini."""
    zones = [_zone("c", 500.0, [0.1, 0.1, 0.4, 0.4])]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    assert len(result) == 1
    assert result[0]["id"] == "c"


def test_no_api_key_ambiguous_zones_kept():
    """Without API key, ambiguous zones (5-50 sqft in drawing area) are kept."""
    zones = [_zone("d", 20.0, [0.1, 0.1, 0.3, 0.3])]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    assert len(result) == 1


def test_all_fields_preserved():
    """Kept zones retain all original fields unchanged."""
    z = _zone("e", 500.0, [0.1, 0.1, 0.4, 0.4])
    z["perimeter_lf"] = 42.5
    result = filter_false_positives([z], "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    assert result[0]["perimeter_lf"] == 42.5


def test_mixed_batch():
    """Large + legend + noise zones: only large kept without Gemini."""
    zones = [
        _zone("keep", 200.0, [0.1, 0.1, 0.4, 0.4]),    # large → keep
        _zone("legend", 150.0, [0.80, 0.1, 0.95, 0.4]), # legend strip → drop
        _zone("noise", 1.0, [0.2, 0.2, 0.25, 0.25]),    # noise → drop
        _zone("ambig", 20.0, [0.3, 0.3, 0.45, 0.45]),   # ambiguous → kept (no key)
    ]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key=None)
    ids = {z["id"] for z in result}
    assert "keep" in ids
    assert "legend" not in ids
    assert "noise" not in ids
    assert "ambig" in ids  # kept because no api_key


def test_gemini_error_fallback(monkeypatch):
    """If Gemini raises, ambiguous zones are kept (silent fallback)."""
    import backend.zone_filter as zf

    monkeypatch.setattr(
        zf,
        "_classify_with_gemini",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("api down")),
    )
    zones = [_zone("f", 20.0, [0.1, 0.1, 0.3, 0.3])]
    result = filter_false_positives(zones, "/fake.pdf", 0, 150, 1 / 16, api_key="fake-key")
    assert len(result) == 1
