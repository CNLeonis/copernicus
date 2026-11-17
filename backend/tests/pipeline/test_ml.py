"""ML tests use synthetic feature values and geometries only."""

import numpy as np
import pytest
from sqlalchemy import text

from app.core.config import FEATURE_COLUMNS
from pipeline.ml import (
    build_experiment_specs,
    cluster_metrics,
    cluster_statistics,
    feature_matrix,
    run,
    run_id,
    scale_matrix,
    select_complete_rows,
)


def sample_row(cell_id, index, *, status="included", land_use="potentially_residential"):
    row = {
        "cell_id": cell_id,
        "area_m2": 62500.0,
        "analysis_status": status,
        "land_use_status": land_use,
    }
    for name in FEATURE_COLUMNS:
        row[name] = float(index + 1)
    return row


def test_select_complete_rows_excludes_slivers_and_missing_values_without_imputation():
    rows = [sample_row(f"cell-{index}", index) for index in range(4)]
    rows[1]["nearest_school_m"] = None
    rows.append(sample_row("sliver", 5, status="excluded_sliver"))
    rows.append(sample_row("incomplete-nonresidential", 6, land_use="no_residential_evidence"))

    complete, counts = select_complete_rows(rows, "potentially_residential")

    assert [row["cell_id"] for row in complete] == ["cell-0", "cell-2", "cell-3"]
    assert counts == {
        "grid_cells": 6,
        "excluded_sliver": 1,
        "included_cells": 5,
        "sample_filter_excluded": 1,
        "missing_feature_excluded": 1,
        "non_finite_feature_excluded": 0,
        "n_samples": 3,
    }


def test_six_counts_keep_cells_without_nearest_distance():
    rows = [sample_row(f"cell-{index}", index) for index in range(4)]
    rows[0]["nearest_school_m"] = None
    rows[1]["schools_500m"] = None

    complete, counts = select_complete_rows(rows, "all", FEATURE_COLUMNS[3:])

    assert [row["cell_id"] for row in complete] == ["cell-0", "cell-2", "cell-3"]
    assert counts["missing_feature_excluded"] == 1
    assert feature_matrix(complete, FEATURE_COLUMNS[3:]).shape == (3, 6)


def test_non_finite_values_are_excluded_and_negative_values_fail():
    rows = [sample_row(f"cell-{index}", index) for index in range(4)]
    rows[0]["shops_500m"] = float("nan")
    complete, counts = select_complete_rows(rows, "all")
    assert len(complete) == 3
    assert counts["non_finite_feature_excluded"] == 1

    rows[0]["shops_500m"] = -1
    with pytest.raises(ValueError, match="Negative accessibility feature"):
        select_complete_rows(rows, "all")


def test_feature_matrix_log_transforms_counts_but_not_distances():
    rows = [sample_row("cell-0", 3)]
    matrix = feature_matrix(rows)

    assert matrix[0, 0] == 4.0
    assert matrix[0, 3] == pytest.approx(np.log1p(4.0))
    assert np.isfinite(scale_matrix(np.vstack([matrix, matrix + 1]))).all()


def test_experiment_specs_cover_kmeans_k2_to_k8_and_one_hdbscan_run():
    specs = build_experiment_specs("all")
    kmeans = [spec for spec in specs if spec["algorithm"] == "kmeans"]
    hdbscan = [spec for spec in specs if spec["algorithm"] == "hdbscan"]

    assert len(kmeans) == 7
    assert {spec["parameters"]["n_clusters"] for spec in kmeans} == set(range(2, 9))
    assert len(hdbscan) == 1
    assert hdbscan[0]["parameters"] == {"min_cluster_size": 30, "min_samples": 10}


def test_metrics_handle_hdbscan_noise_and_undefined_single_cluster():
    matrix = np.asarray([[0.0], [0.1], [0.2], [5.0], [5.1], [5.2], [99.0]])
    labels = np.asarray([0, 0, 0, 1, 1, 1, -1])

    metrics = cluster_metrics(matrix, labels, noise_label=-1)
    assert metrics["silhouette"] is not None
    assert metrics["evaluated_fraction"] == pytest.approx(6 / 7)

    undefined = cluster_metrics(matrix[:3], np.asarray([0, 0, 0]))
    assert undefined == {"silhouette": None, "davies_bouldin": None, "calinski_harabasz": None}


def test_cluster_statistics_keep_noise_and_describe_original_features():
    rows = [sample_row("cell-a", 1), sample_row("cell-b", 3), sample_row("cell-noise", 100)]
    stats = cluster_statistics(rows, np.asarray([0, 0, -1]))

    assert [row["cluster_label"] for row in stats] == [-1, 0]
    assert stats[0]["cell_count"] == 1
    assert stats[1]["cell_count"] == 2
    assert stats[1]["feature_statistics"]["nearest_shop_m"]["median"] == 3.0
    assert sum(row["sample_share"] for row in stats) == pytest.approx(1.0)


def test_run_ids_are_stable_for_grid_and_configuration():
    config = {"algorithm": "kmeans", "parameters": {"n_clusters": 2}}

    assert run_id(250, config) == run_id(250, config)
    assert run_id(250, config) != run_id(500, config)


def seed_ml_dataset(db):
    with db.begin() as connection:
        for index in range(12):
            cell_id = f"g250-c{index:03d}-r000"
            x = 400000 + index * 250
            connection.execute(text(
                "INSERT INTO analysis_cells (id, cell_size_m, col, row, geometry, full_geometry, "
                "representative_point, representative_point_method, point_land_cover, area_m2, building_count, "
                "residential_area_m2, land_use_status, analysis_status) VALUES "
                "(:id, 250, :col, 0, ST_Multi(ST_MakeEnvelope(:x, 560000, :x + 250, 560250, 2180)), "
                "ST_MakeEnvelope(:x, 560000, :x + 250, 560250, 2180), ST_SetSRID(ST_MakePoint(:x + 125, "
                "560125), 2180), 'centroid', 'ok', 62500, 1, 62500, 'potentially_residential', 'included')"
            ), {"id": cell_id, "col": index, "x": x})
            values = [float(index % 2 * 100 + feature_index + 1) for feature_index in range(9)]
            columns = ", ".join(FEATURE_COLUMNS)
            placeholders = ", ".join(f":v{i}" for i in range(9))
            connection.execute(text(
                "INSERT INTO cell_features (cell_id, " + columns +
                ", shop_status, school_status, bus_stop_status, buffer_sufficient) VALUES "
                "(:cell, " + placeholders + ", 'ok', 'ok', 'ok', true)"
            ), {"cell": cell_id,
                **{f"v{i}": value for i, value in enumerate(values)}})
    return 250


@pytest.mark.db
def test_pipeline_persists_run_assignments_and_is_idempotent(db):
    cell_size_m = seed_ml_dataset(db)
    kwargs = {
        "cell_size_m": cell_size_m,
        "algorithm": "kmeans",
        "k_values": (2,),
        "engine": db,
    }

    first = run(**kwargs)
    second = run(**kwargs)

    assert len(first["runs"]) == 1
    assert first["runs"][0]["written"] is True
    assert second["runs"][0]["written"] is False
    assert first["runs"][0]["n_samples"] == 12
    with db.connect() as connection:
        run_row = connection.execute(text(
            "SELECT cell_size_m, algorithm, random_state, n_samples, is_published, parameters "
            "FROM ml_runs WHERE id = :id"
        ), {"id": first["runs"][0]["id"]}).mappings().one()
        assignment_count = connection.execute(text(
            "SELECT count(*) FROM cluster_assignments WHERE run_id = :id"
        ), {"id": first["runs"][0]["id"]}).scalar_one()
        stats = connection.execute(text(
            "SELECT sum(cell_count), sum(sample_share) FROM cluster_statistics WHERE run_id = :id"
        ), {"id": first["runs"][0]["id"]}).one()

    assert run_row["cell_size_m"] == cell_size_m
    assert run_row["algorithm"] == "kmeans"
    assert run_row["random_state"] == 42
    assert run_row["is_published"] is True
    assert run_row["parameters"]["n_clusters"] == 2
    assert run_row["parameters"]["feature_set"] == "counts_six"
    assert assignment_count == 12
    assert stats[0] == 12
    assert stats[1] == pytest.approx(1.0)


@pytest.mark.db
def test_pipeline_counts_cells_without_feature_rows_as_missing(db):
    cell_size_m = seed_ml_dataset(db)
    with db.begin() as connection:
        connection.execute(text(
            "DELETE FROM cell_features WHERE cell_id = ("
            "SELECT id FROM analysis_cells WHERE cell_size_m = :size ORDER BY id LIMIT 1)"
        ), {"size": cell_size_m})

    result = run(
        cell_size_m=cell_size_m,
        algorithm="kmeans",
        k_values=(2,),
        engine=db,
    )

    assert result["sample_counts"]["grid_cells"] == 12
    assert result["sample_counts"]["missing_feature_excluded"] == 1
    assert result["sample_counts"]["n_samples"] == 11


@pytest.mark.db
def test_six_count_default_keeps_legacy_run_and_more_cells(db):
    size = seed_ml_dataset(db)
    with db.begin() as connection:
        connection.execute(text(
            "UPDATE cell_features SET nearest_school_m = NULL "
            "WHERE cell_id = (SELECT id FROM analysis_cells ORDER BY id LIMIT 1)"
        ))

    legacy = run(cell_size_m=size, algorithm="kmeans", k_values=(2,),
                 feature_set="all_nine", engine=db)["runs"][0]
    default = run(cell_size_m=size, engine=db)["runs"][0]

    assert legacy["n_samples"] == 11
    assert default["n_samples"] == 12
    assert legacy["id"] != default["id"]
    with db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ml_runs")).scalar_one() == 2
