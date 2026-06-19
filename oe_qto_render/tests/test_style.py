"""The style contract is locked: 14 elements, fixed order, locked attributes."""
from oe_qto_render import style
from oe_qto_render.style import ELEMENTS, Geometry, Unit


def test_exactly_14_elements_in_fixed_order():
    assert len(ELEMENTS) == 14
    assert style.ORDER == (
        "coping", "total_sf", "tanning_ledge", "waterline", "bench", "steps",
        "toe_tile", "planter", "stone_steppers", "lights", "drain_line",
        "light_run", "d_markers", "skimmers",
    )


def test_each_element_has_locked_attributes():
    for e in ELEMENTS:
        assert e.color.startswith("#") and len(e.color) == 7
        assert isinstance(e.geometry, Geometry)
        assert isinstance(e.unit, Unit)
        # geometry <-> unit consistency
        if e.geometry is Geometry.LINEAR:
            assert e.unit is Unit.FT
        elif e.geometry is Geometry.AREA:
            assert e.unit is Unit.SQ_FT
        else:
            assert e.unit is Unit.COUNT


def test_specific_locked_colors():
    assert style.BY_KEY["coping"].color == "#FF9800"
    assert style.BY_KEY["total_sf"].color == "#FF00DB"
    assert style.BY_KEY["total_sf"].opacity == 0.302
    assert style.BY_KEY["light_run"].color == "#E91E63"
    assert style.BY_KEY["skimmers"].color == "#00FFA0"


def test_area_elements_are_semi_transparent():
    for key in ("total_sf", "tanning_ledge", "stone_steppers"):
        assert style.BY_KEY[key].geometry is Geometry.AREA
        assert 0.25 < style.BY_KEY[key].opacity < 0.5


def test_total_sf_is_first_area_element():
    """Total SF must underlie the other area fills (lowest in the area stack)."""
    area_keys = [e.key for e in ELEMENTS if e.geometry is Geometry.AREA]
    assert area_keys[0] == "total_sf"
