"""Plan-data model validation."""
import pytest
from pydantic import ValidationError

from oe_qto_render.model import PlanData


def _base():
    return {"pdf": "base.pdf", "page_index": 3, "width": 100.0, "height": 80.0}


def test_minimal_valid_plan():
    pd = PlanData(
        base=_base(),
        elements={
            "coping": {"total": 199.92, "segments": [[[0, 0], [10, 0]]]},
            "total_sf": {"total": "1109.23", "polygons": [[[0, 0], [1, 0], [1, 1]]]},
            "lights": {"total": 2, "points": [[5, 5], [6, 6]]},
            "light_run": {"total": 50.0, "origin": [0, 0]},
        },
    )
    assert pd.base.page_index == 3
    ordered = pd.ordered()
    assert len(ordered) == 14
    assert ordered[0][0] == "coping"


def test_unknown_element_rejected():
    with pytest.raises(ValidationError):
        PlanData(base=_base(), elements={"jacuzzi": {"total": 1, "points": []}})


def test_linear_requires_segments():
    with pytest.raises(ValidationError):
        PlanData(base=_base(), elements={"coping": {"total": 1.0}})


def test_area_requires_polygons():
    with pytest.raises(ValidationError):
        PlanData(base=_base(), elements={"total_sf": {"total": 1.0}})


def test_light_run_requires_origin():
    with pytest.raises(ValidationError):
        PlanData(base=_base(), elements={"light_run": {"total": 1.0}})


def test_empty_points_list_is_valid():
    pd = PlanData(base=_base(), elements={"skimmers": {"total": 0, "points": []}})
    assert pd.elements["skimmers"].points == []
