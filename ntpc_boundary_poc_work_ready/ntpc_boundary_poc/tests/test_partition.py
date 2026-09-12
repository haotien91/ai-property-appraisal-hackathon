from shapely.geometry import LineString, Point, box

from src.partition import build_barriers, partition_roi, select_candidate


def _roads():
    return {
        "north_of": LineString([(-15, -10), (15, -10)]),
        "west_of": LineString([(10, -15), (10, 15)]),
        "south_of": LineString([(-15, 10), (15, 10)]),
        "east_of": LineString([(-10, -15), (-10, 15)]),
    }


def test_partition_selects_anchor_cell_inside_four_boundaries():
    roi = box(-20, -20, 20, 20)
    anchor = Point(0, 0)
    roads = _roads()
    barriers = build_barriers(roads, 1.0)
    cells = partition_roi(roi, barriers)
    selected = select_candidate(cells, anchor, roads)
    assert selected.contains(anchor)
    minx, miny, maxx, maxy = selected.bounds
    assert minx >= -10 and maxx <= 10
    assert miny >= -10 and maxy <= 10


def test_candidate_rejects_wrong_side_of_boundary():
    roi = box(-20, -20, 20, 20)
    anchor = Point(0, 0)
    roads = _roads()
    barriers = build_barriers(roads, 1.0)
    cells = partition_roi(roi, barriers)
    wrong_rules = dict(roads)
    wrong_rules["north_of"] = LineString([(-15, 5), (15, 5)])
    import pytest
    with pytest.raises(ValueError):
        select_candidate(cells, anchor, wrong_rules)
