"""Renderer acceptance checks (spec §9) against the emitted SVG. Runs with
`with_base=False` so no base PDF is needed and the test is fast/deterministic."""
import re

from oe_qto_render import style
from oe_qto_render.model import PlanData
from oe_qto_render.renderer import render_svg


def _plan():
    return PlanData.model_validate({
        "base": {"pdf": "x.pdf", "width": 1000.0, "height": 800.0},
        "elements": {
            "coping": {"total": 199.92, "segments": [[[10, 10], [200, 10]]]},
            "total_sf": {"total": 1109.23, "polygons": [[[10, 10], [200, 10], [200, 300]]]},
            "tanning_ledge": {"total": 290.47, "polygons": [[[20, 20], [40, 20], [40, 40]]]},
            "waterline": {"total": 180.17, "segments": [[[10, 12], [200, 12]]]},
            "bench": {"total": 43.29, "segments": [[[10, 20], [50, 20]]]},
            "steps": {"total": 48.39, "segments": [[[10, 30], [50, 30]]]},
            "toe_tile": {"total": 94.64, "segments": [[[10, 40], [50, 40]]]},
            "planter": {"total": 27.71, "segments": [[[10, 50], [50, 50]]]},
            "stone_steppers": {"total": 41.09, "polygons": [[[60, 60], [80, 60], [80, 80]]]},
            "lights": {"total": 3, "points": [[100, 100], [120, 100], [140, 100]]},
            "drain_line": {"total": "46.6", "segments": [[[10, 60], [50, 60]]]},
            "light_run": {"total": 658.52, "origin": [5, 5]},
            "d_markers": {"total": 2, "points": [[100, 200], [120, 200]]},
            "skimmers": {"total": 1, "points": [[100, 300]]},
        },
        "legend_origin": [600, 100],
    })


def test_renders_svg():
    svg = render_svg(_plan(), with_base=False)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_all_14_legend_names_present_in_order():
    svg = render_svg(_plan(), with_base=False)
    names = [e.name for e in style.ELEMENTS]
    positions = [svg.index(f">{n}<") for n in names]
    assert positions == sorted(positions), "legend names not in fixed order"


def test_locked_colors_present():
    svg = render_svg(_plan(), with_base=False)
    for e in style.ELEMENTS:
        assert e.color in svg, f"{e.key} color {e.color} missing"


def test_formatted_values_present():
    svg = render_svg(_plan(), with_base=False)
    for txt in ("199.92 ft", "1,109.23 sq ft", "290.47 sq ft", "46.6 ft",
                "658.52 ft", ">12<".replace("12", "3")):
        assert txt in svg
    # counts render as bare integers (no unit)
    assert ">3<" in svg and ">2<" in svg and ">1<" in svg


def test_one_light_run_leader_per_light():
    svg = render_svg(_plan(), with_base=False)
    crimson = style.BY_KEY["light_run"].color
    # leaders all originate from the single origin (5,5); 3 lights -> 3 leaders.
    # (a 4th crimson line is the legend's Light-run symbol swatch, elsewhere.)
    leaders = re.findall(rf'<line x1="5.000" y1="5.000"[^>]*stroke="{crimson}"', svg)
    assert len(leaders) == 3


def test_total_sf_fill_drawn_before_other_areas():
    svg = render_svg(_plan(), with_base=False)
    i_total = svg.index('fill="#FF00DB" fill-opacity')
    i_tan = svg.index('fill="#009688" fill-opacity')
    assert i_total < i_tan, "Total SF must underlie other area fills"


def test_legend_box_is_white_rounded_with_border():
    svg = render_svg(_plan(), with_base=False)
    assert re.search(r'<rect[^>]*rx="14[^>]*fill="#FFFFFF"[^>]*'
                     rf'stroke="{style.LEGEND_BORDER}"', svg)
