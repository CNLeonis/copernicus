import pytest

from pipeline.assessment import assess_row, classify_category, classify_overall


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

    result = assess_row(row, "potentially_residential")

    assert result["accessibility_class"] == "medium"
    assert result["accessibility_score"] == pytest.approx(0.5)
    assert result["criteria_results"]["categories"] == {
        "shop": {"class": "low", "count_500m": 0, "count_1000m": 0},
        "school": {"class": "medium", "count_500m": 0, "count_1000m": 1},
        "bus_stop": {"class": "high", "count_500m": 2, "count_1000m": 4},
    }


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

    result = assess_row(row, "all")

    assert result["accessibility_class"] == "low"
    assert result["accessibility_score"] is None
    assert result["criteria_results"]["partial_data_note"]
