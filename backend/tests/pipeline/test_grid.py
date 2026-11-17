"""Grid tests on SYNTHETIC metric geometries (not real Opole data)."""

import pytest
from shapely import MultiPolygon, Point, box
from sqlalchemy import text

from pipeline import grid, import_osm
from pipeline.grid import build_cells, cell_id, choose_point, grid_origin, summarize

X0, Y0 = 500_000, 300_000
# 1000 m x 520 m: with 250 m cells the top row is 20 m high (8% of a cell) -> slivers
CITY = MultiPolygon([box(X0, Y0, X0 + 1000, Y0 + 520)])


def cells_by_key(cells):
    return {(c["col"], c["row"]): c for c in cells}


def test_ids_are_stable_and_readable():
    assert cell_id(250, 3, 12) == "g250-c003-r012"


def test_origin_is_aligned_so_grids_nest():
    assert grid_origin((X0 + 123.4, Y0 + 987.6, 0, 0)) == (X0, Y0)


def test_cells_cover_city_and_mark_slivers():
    cells = build_cells(CITY, 250, {})
    assert len(cells) == 4 * 3
    assert sum(c["area_m2"] for c in cells) == pytest.approx(CITY.area)
    assert all(c["full_geometry"].area == pytest.approx(62_500) for c in cells)
    slivers = [c for c in cells if c["analysis_status"] == "excluded_sliver"]
    assert {c["row"] for c in slivers} == {2} and len(slivers) == 4
    assert all(c["area_m2"] == pytest.approx(250 * 20) for c in slivers)


def test_250_m_cells_nest_inside_500_m_cells():
    small = cells_by_key(build_cells(CITY, 250, {}))
    large = cells_by_key(build_cells(CITY, 500, {}))
    for (col, row), cell in small.items():
        assert large[(col // 2, row // 2)]["full_geometry"].covers(cell["full_geometry"])


def test_centroid_outside_concave_cell_falls_back_to_representative_point():
    # U-shaped fragment: the centroid of the cell lies in the cut-out
    city = MultiPolygon([box(X0, Y0, X0 + 250, Y0 + 250).difference(box(X0 + 50, Y0 + 50, X0 + 200, Y0 + 250))])
    (cell,) = build_cells(city, 250, {})
    assert cell["representative_point_method"] == "representative_point"
    assert cell["geometry"].contains(cell["representative_point"])


def test_point_avoids_water_and_reports_cells_fully_covered():
    water = box(X0 + 50, Y0, X0 + 200, Y0 + 250)  # river through the middle of cell (0, 0)
    lake = box(X0 + 250, Y0, X0 + 500, Y0 + 250)  # covers cell (1, 0) entirely
    cells = cells_by_key(build_cells(CITY, 250, {"water": [water, lake]}))
    river_cell, lake_cell = cells[(0, 0)], cells[(1, 0)]
    assert river_cell["representative_point_method"] == "representative_point"
    assert river_cell["point_land_cover"] == "ok"
    assert not water.covers(river_cell["representative_point"])
    assert lake_cell["point_land_cover"] == "water"
    assert cells[(2, 0)]["representative_point_method"] == "centroid"


def test_choose_point_uses_centroid_when_suitable():
    point, method = choose_point(box(0, 0, 10, 10), box(100, 100, 110, 110))
    assert method == "centroid" and point.equals(Point(5, 5))


def test_point_ignores_land_slivers_and_prefers_largest_land_part():
    cell = box(0, 0, 250, 250)
    forest = box(0.001, 0, 200, 250)  # leaves a 0.25 m² strip on the left and 50 m on the right
    point, method = choose_point(cell, forest)
    assert method == "representative_point" and point.x > 200


def test_buildings_are_counted_once_and_residential_area_is_clipped():
    across_edge = box(X0 + 240, Y0 + 10, X0 + 270, Y0 + 20)  # point on surface in cell (1, 0)
    inside = box(X0 + 10, Y0 + 10, X0 + 20, Y0 + 20)
    outside_city = box(X0 - 100, Y0 + 10, X0 - 90, Y0 + 20)
    residential = box(X0 + 200, Y0 + 200, X0 + 300, Y0 + 300)  # 50x50 in each of four cells
    cells = cells_by_key(build_cells(CITY, 250, {
        "building": [across_edge, inside, outside_city], "residential": [residential, residential]}))
    assert sum(c["building_count"] for c in cells.values()) == 2
    assert cells[(0, 0)]["building_count"] == 1
    assert cells[(0, 0)]["residential_area_m2"] == pytest.approx(2_500)  # overlap is not double-counted
    assert cells[(0, 0)]["land_use_status"] == "potentially_residential"
    assert cells[(3, 1)]["land_use_status"] == "no_residential_evidence"


def test_summary_counts_cells_and_evidence():
    cells = build_cells(CITY, 250, {"residential": [box(X0, Y0, X0 + 10, Y0 + 10)]})
    summary = summarize(cells, CITY)
    assert summary["cells"] == 12
    assert summary["analysis_status"] == {"included": 8, "excluded_sliver": 4}
    assert summary["cells_area_km2"] == summary["city_area_km2"]
    assert summary["included_residential_evidence"] == {"residential_landuse": 1, "none": 7}


@pytest.mark.db
def test_run_writes_grids_once_in_metric_crs(db, monkeypatch):
    import_osm.write_import(db, {"id": 1, "tags": {"name": "SYNTHETIC"}}, CITY,
                            [{"source_type": "node", "source_id": 1, "name": None, "category": "shop",
                              "subcategory": "bakery", "geometry": Point(X0 + 5, Y0 + 5),
                              "inside_city": True}],
                            [{"source_type": "way", "source_id": 1, "kind": "building",
                              "geometry": box(X0 + 10, Y0 + 10, X0 + 20, Y0 + 20)}], replace=False)
    monkeypatch.setattr(grid, "get_engine", lambda: db)

    first = grid.run([250, 500])
    assert [r["written"] for r in first] == [True, True]
    assert [r["written"] for r in grid.run([250])] == [False]
    assert grid.run([250], replace=True)[0]["written"]
    with db.connect() as c:
        rows = c.execute(text(
            "SELECT a.cell_size_m, count(*), sum(a.building_count), min(ST_SRID(a.representative_point)), "
            "round(sum(ST_Area(a.geometry))) FROM analysis_cells a "
            "GROUP BY a.cell_size_m ORDER BY a.cell_size_m")).all()
    assert [tuple(r) for r in rows] == [(250, 12, 1, 2180, 520_000), (500, 4, 1, 2180, 520_000)]
