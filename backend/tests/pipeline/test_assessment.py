import pytest
from sqlalchemy import text

from pipeline.assessment import assess_row, classify_category, classify_overall, run


@pytest.mark.parametrize(
    ("count_500m", "count_1000m", "expected"),
    [
        (1, 1, "high"),
        (3, 7, "high"),
        (0, 1, "medium"),
        (0, 0, "low"),
        (None, 0, "no_data"),
        (0, None, "no_data"),
    ],
)
def test_classify_category(count_500m, count_1000m, expected):
    assert classify_category(count_500m, count_1000m) == expected


@pytest.mark.parametrize("counts", [(-1, 0), (1, 0), (0.0, 0), (None, -1), (True, None)])
def test_classify_category_rejects_invalid_counts(counts):
    with pytest.raises(ValueError):
        classify_category(*counts)


@pytest.mark.parametrize(
    ("classes", "expected"),
    [
        ({"shop": "low", "school": "low", "bus_stop": "no_data"}, "low"),
        ({"shop": "low", "school": "high", "bus_stop": "high"}, "medium"),
        ({"shop": "medium", "school": "medium", "bus_stop": "high"}, "medium"),
        ({"shop": "high", "school": "medium", "bus_stop": "high"}, "high"),
        ({"shop": "high", "school": "no_data", "bus_stop": "high"}, "no_data"),
    ],
)
def test_classify_overall(classes, expected):
    result, _score = classify_overall(classes)
    assert result == expected


def test_assess_row_preserves_known_zeros_and_reports_reasons():
    row = {
        "cell_id": "synthetic-cell",
        "shops_500m": 0,
        "shops_1000m": 0,
        "schools_500m": 0,
        "schools_1000m": 1,
        "bus_stops_500m": 2,
        "bus_stops_1000m": 4,
    }

    result = assess_row(row)

    assert result["accessibility_class"] == "medium"
    assert result["accessibility_score"] == pytest.approx(0.5)
    assert result["criteria_results"]["categories"] == {
        "shop": {"class": "low", "count_500m": 0, "count_1000m": 0},
        "school": {"class": "medium", "count_500m": 0, "count_1000m": 1},
        "bus_stop": {"class": "high", "count_500m": 2, "count_1000m": 4},
    }
    assert "sample_filter" not in result["criteria_results"]
    assert "method" not in result["criteria_results"]


def test_two_known_low_categories_can_classify_cell_despite_third_missing():
    row = {
        "cell_id": "synthetic-cell",
        "shops_500m": 0,
        "shops_1000m": 0,
        "schools_500m": 0,
        "schools_1000m": 0,
        "bus_stops_500m": None,
        "bus_stops_1000m": None,
    }

    result = assess_row(row)

    assert result["accessibility_class"] == "low"
    assert result["accessibility_score"] is None
    assert result["criteria_results"]["partial_data_note"]


@pytest.mark.db
def test_assessment_reads_current_features_and_is_idempotent(db):
    with db.begin() as connection:
        connection.execute(text(
            "INSERT INTO analysis_cells (id, cell_size_m, col, row, geometry, full_geometry, "
            "representative_point, representative_point_method, point_land_cover, area_m2, "
            "building_count, residential_area_m2, land_use_status, analysis_status) VALUES "
            "('g250-c000-r000', 250, 0, 0, ST_Multi(ST_MakeEnvelope(0, 0, 250, 250, 2180)), "
            "ST_MakeEnvelope(0, 0, 250, 250, 2180), ST_SetSRID(ST_MakePoint(125, 125), 2180), "
            "'centroid', 'ok', 62500, 1, 0, 'potentially_residential', 'included')"
        ))
        connection.execute(text(
            "INSERT INTO cell_features (cell_id, shops_500m, shops_1000m, schools_500m, "
            "schools_1000m, bus_stops_500m, bus_stops_1000m, shop_status, school_status, "
            "bus_stop_status, buffer_sufficient) VALUES "
            "('g250-c000-r000', 0, 0, 0, 1, 1, 1, 'ok', 'ok', 'ok', true)"
        ))

    assert run(engine=db)["written"] is True
    assert run(engine=db)["written"] is False
    with db.connect() as connection:
        row = connection.execute(text(
            "SELECT accessibility_class, accessibility_score FROM accessibility_results "
            "WHERE cell_id = 'g250-c000-r000'"
        )).one()
    assert row[0] == "medium"
    assert row[1] == pytest.approx(0.5)
