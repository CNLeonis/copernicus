"""Feature tests use synthetic points and geometries; no real OSM data is queried."""

import pytest
from shapely import MultiPolygon, Point, box
from sqlalchemy import text

from pipeline import features, grid, import_osm
from pipeline.features import calculate_cell_features


def cell(x=0, y=0, cell_id="synthetic-cell", coverage_radius_m=None):
    result = {"id": cell_id, "representative_point": Point(x, y)}
    if coverage_radius_m is not None:
        result["coverage_radius_m"] = coverage_radius_m
    return result


def test_calculates_distances_and_radius_counts_in_metres():
    rows = calculate_cell_features(
        [cell()],
        {
            "shop": [Point(300, 0), Point(-500, 0)],
            "school": [Point(0, 1001)],
            "bus_stop": [Point(0, 0)],
        },
        source_buffer_m=2000,
    )

    (row,) = rows
    assert row["nearest_shop_m"] == pytest.approx(300)
    assert row["nearest_school_m"] == pytest.approx(1001)
    assert row["nearest_bus_stop_m"] == pytest.approx(0)
    assert (row["shops_500m"], row["shops_1000m"]) == (2, 2)
    assert (row["schools_500m"], row["schools_1000m"]) == (0, 0)
    assert (row["bus_stops_500m"], row["bus_stops_1000m"]) == (1, 1)
    assert row["shop_status"] == row["school_status"] == row["bus_stop_status"] == "ok"
    assert row["buffer_sufficient"] is True


def test_missing_source_is_null_not_zero_and_not_sufficient():
    (row,) = calculate_cell_features(
        [cell()], {"shop": [], "school": [Point(10, 0)], "bus_stop": [Point(0, 0)]},
        source_buffer_m=2000,
    )

    assert row["shop_status"] == "no_source"
    assert row["nearest_shop_m"] is None
    assert row["shops_500m"] is None and row["shops_1000m"] is None
    assert row["school_status"] == "ok"
    assert row["schools_500m"] == 1
    assert row["bus_stops_500m"] == 1
    assert row["buffer_sufficient"] is False


def test_zero_count_is_distinct_from_category_with_no_nearby_object():
    (row,) = calculate_cell_features(
        [cell()], {"shop": [Point(1500, 0)], "school": [Point(0, 0)], "bus_stop": [Point(0, 0)]},
        source_buffer_m=2000,
    )

    assert row["shop_status"] == "ok"
    assert row["nearest_shop_m"] == pytest.approx(1500)
    assert row["shops_500m"] == 0 and row["shops_1000m"] == 0


def test_far_candidate_outside_proven_coverage_is_censored():
    (row,) = calculate_cell_features(
        [cell()], {"shop": [Point(2501, 0)], "school": [Point(0, 0)], "bus_stop": [Point(0, 0)]},
        source_buffer_m=2000,
    )

    assert row["nearest_shop_m"] is None
    assert row["shop_status"] == "none_found"
    assert row["shops_500m"] == 0 and row["shops_1000m"] == 0
    assert row["buffer_sufficient"] is False


def test_short_source_buffer_leaves_out_of_coverage_counts_null():
    (row,) = calculate_cell_features(
        [cell(coverage_radius_m=2000)],
        {"shop": [Point(1200, 0)], "school": [Point(10, 0)], "bus_stop": [Point(0, 0)]},
        source_buffer_m=750,
    )

    assert row["nearest_shop_m"] == pytest.approx(1200)
    assert row["shops_500m"] == 0
    assert row["shops_1000m"] is None
    assert row["schools_500m"] == 1
    assert row["buffer_sufficient"] is False


def test_invalid_radius_fails_clearly():
    with pytest.raises(ValueError, match="positive"):
        calculate_cell_features([cell()], {}, source_buffer_m=2000, radii_m=(0,))


@pytest.mark.db
def test_run_writes_versioned_features_once_and_can_replace(db, monkeypatch):
    origin_x, origin_y = 500_000, 300_000
    city = MultiPolygon([box(origin_x, origin_y, origin_x + 250, origin_y + 250)])
    representative = Point(origin_x + 125, origin_y + 125)
    services = [
        {"source_type": "node", "source_id": 1, "name": "SYNTHETIC shop", "category": "shop",
         "subcategory": "supermarket", "geometry": Point(origin_x + 225, origin_y + 125),
         "source_tags": {"shop": "supermarket"}, "inside_city": True},
        {"source_type": "node", "source_id": 2, "name": "SYNTHETIC school", "category": "school",
         "subcategory": "primary", "geometry": representative,
         "source_tags": {"amenity": "school"}, "inside_city": True},
        {"source_type": "node", "source_id": 3, "name": "SYNTHETIC stop", "category": "bus_stop",
         "subcategory": "bus_tram_stop", "geometry": Point(origin_x + 125, origin_y + 225),
         "source_tags": {"highway": "bus_stop"}, "inside_city": True},
    ]
    monkeypatch.setattr(import_osm, "DATA_VERSION", "SYNTHETIC-features")
    import_osm.write_import(
        db,
        {"downloaded_at": "2026-01-01T00:00:00+00:00", "buffer_m": 2000},
        {"id": 99, "tags": {"name": "SYNTHETIC"}},
        city,
        services,
        [],
        replace=False,
    )
    monkeypatch.setattr(grid, "get_engine", lambda: db)
    grid.run([250], "SYNTHETIC-features")
    monkeypatch.setattr(features, "get_engine", lambda: db)

    first = features.run()
    assert first["written"] is True
    assert first["cell_count"] == 1
    assert first["buffer_sufficient_cells"] == 1
    assert features.run()["written"] is False

    with db.connect() as connection:
        row = connection.execute(text(
            "SELECT nearest_shop_m, nearest_school_m, nearest_bus_stop_m, shops_500m, shops_1000m, "
            "schools_500m, schools_1000m, bus_stops_500m, bus_stops_1000m, buffer_sufficient "
            "FROM cell_features WHERE feature_version = :version"),
            {"version": first["feature_version"]}).one()
    assert tuple(row) == (100.0, 0.0, 100.0, 1, 1, 1, 1, 1, 1, True)

    replaced = features.run(replace=True)
    assert replaced["written"] is True
    assert replaced["feature_version"] == first["feature_version"]
