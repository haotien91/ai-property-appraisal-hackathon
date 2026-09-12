from src.crs import anchor_3826, roi_square, to_4326


def test_anchor_and_roi_are_metric():
    p = anchor_3826(24.99384, 121.421118)
    roi = roi_square(p, 600)
    assert 359000 < roi.area < 361000
    assert roi.contains(p)


def test_round_trip_to_4326_is_close_to_input():
    p = anchor_3826(24.99384, 121.421118)
    wgs = to_4326(p)
    assert abs(wgs.y - 24.99384) < 1e-6
    assert abs(wgs.x - 121.421118) < 1e-6
