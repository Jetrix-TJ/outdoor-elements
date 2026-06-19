"""Number/unit formatting rules (spec §6)."""
from oe_qto_render import style
from oe_qto_render.format import format_number, format_value, format_count


def test_two_decimals_with_comma_thousands():
    assert format_number(1109.23) == "1,109.23"
    assert format_number(199.92) == "199.92"
    assert format_number(48.39) == "48.39"


def test_string_value_preserves_source_decimals():
    # reference shows "46.6" (one decimal) — keep it verbatim
    assert format_number("46.6") == "46.6"
    assert format_number("1109.23") == "1,109.23"


def test_counts_are_bare_integers():
    assert format_count(12) == "12"
    assert format_count("8") == "8"


def test_format_value_adds_unit_suffix():
    assert format_value(style.BY_KEY["coping"], 199.92) == "199.92 ft"
    assert format_value(style.BY_KEY["total_sf"], 1109.23) == "1,109.23 sq ft"
    assert format_value(style.BY_KEY["drain_line"], "46.6") == "46.6 ft"


def test_format_value_count_has_no_unit():
    assert format_value(style.BY_KEY["lights"], 12) == "12"
    assert format_value(style.BY_KEY["skimmers"], 3) == "3"
