"""Per-zone storage + extraction + delete/restore."""
import numpy as np

from backend import db, store, zones


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()


def test_replace_and_list_zones():
    _fresh_db()
    store.replace_zones("job1", 0, [
        {"code": "M.5", "hex": "#ff0000", "area_sqft": 100.0,
         "geometry": [[[0, 0], [10, 0], [10, 10]]], "bbox": [0, 0, 0.1, 0.1], "source": "engine"},
        {"code": "M.5", "hex": "#ff0000", "area_sqft": 50.0,
         "geometry": [[[20, 20], [30, 20], [30, 30]]], "bbox": [0.2, 0.2, 0.3, 0.3]},
    ])
    active = store.list_zones("job1", 0)
    assert len(active) == 2
    assert all(z["id"] for z in active)            # every zone has a stable id
    assert active[0]["area_sqft"] == 100.0          # ordered by area desc


def test_soft_delete_and_restore():
    _fresh_db()
    ids = store.replace_zones("job1", 0, [
        {"code": "M.5", "area_sqft": 100.0, "geometry": [[[0, 0], [1, 0], [1, 1]]]},
        {"code": "M.6", "area_sqft": 40.0, "geometry": [[[2, 2], [3, 2], [3, 3]]]},
    ])
    store.set_zone_status(ids[0], "deleted")
    assert len(store.active_zones("job1", 0)) == 1
    assert len(store.list_zones("job1", 0, include_deleted=True)) == 2
    store.set_zone_status(ids[0], "active")
    assert len(store.active_zones("job1", 0)) == 2


def test_replace_zones_clears_previous():
    _fresh_db()
    store.replace_zones("job1", 0, [{"code": "M.5", "geometry": [[[0, 0], [1, 0], [1, 1]]]}])
    store.replace_zones("job1", 0, [{"code": "M.6", "geometry": [[[0, 0], [1, 0], [1, 1]]]}])
    active = store.active_zones("job1", 0)
    assert len(active) == 1 and active[0]["code"] == "M.6"


def test_set_zones_status_by_code():
    _fresh_db()
    store.replace_zones("job1", 0, [
        {"code": "M.5", "geometry": [[[0, 0], [1, 0], [1, 1]]]},
        {"code": "M.5", "geometry": [[[2, 2], [3, 2], [3, 3]]]},
        {"code": "M.6", "geometry": [[[4, 4], [5, 4], [5, 5]]]},
    ])
    ids = store.set_zones_status_by_code("job1", 0, "M.5", "deleted")
    assert len(ids) == 2
    remaining = store.active_zones("job1", 0)
    assert len(remaining) == 1 and remaining[0]["code"] == "M.6"


def test_extract_zones_from_label():
    # two separate M.5 blobs + one M.6 blob -> 3 zones
    label = np.zeros((100, 100), np.uint8)
    label[10:30, 10:30] = 1     # M.5 region A
    label[60:80, 60:80] = 1     # M.5 region B (separate component)
    label[10:30, 60:80] = 2     # M.6 region
    zlist = zones.extract_zones_from_label(label, ["M.5", "M.6"], scale=1 / 16, dpi=150)
    by_code = {}
    for z in zlist:
        by_code.setdefault(z["code"], 0)
        by_code[z["code"]] += 1
        assert z["geometry"] and len(z["geometry"][0]) >= 3   # has a polygon
        assert z["area_sqft"] > 0
    assert by_code["M.5"] == 2      # two connected components -> two zones
    assert by_code["M.6"] == 1


def test_groups_from_zones_aggregates_by_code():
    z = [
        {"code": "M.5", "area_sqft": 100.0, "hex": "#ff0000"},
        {"code": "M.5", "area_sqft": 50.0, "hex": "#ff0000"},
        {"code": "M.6", "area_sqft": 40.0, "hex": "#00ff00"},
    ]
    groups = zones.groups_from_zones(z)
    g = {x["label"]: x for x in groups}
    assert g["M.5"]["sqft"] == 150.0 and g["M.5"]["regions"] == 2
    assert g["M.6"]["sqft"] == 40.0
    assert groups[0]["label"] == "M.5"      # ordered by area desc
