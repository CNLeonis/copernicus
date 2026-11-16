"""ML tests use synthetic feature values and geometries only."""

import numpy as np
import pytest
from sqlalchemy import text

from app.core.config import FEATURE_COLUMNS
from pipeline.ml import (
    build_experiment_specs,
    cluster_metrics,
    cluster_statistics,
    feature_diagnostics,
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
    assert np.isfinite(scale_matrix(np.vstack([matrix, matrix + 1]), "robust")).all()


def test_feature_diagnostics_flags_outliers_without_removing_observations():
    matrix = np.ones((5, len(FEATURE_COLUMNS)), dtype=float)
    matrix[-1, 0] = 100.0

    report = feature_diagnostics(matrix)

    assert report["features"]["nearest_shop_m"]["outlier_count"] == 1
    assert "no rows removed" in report["rule"]


def test_experiment_matrix_covers_kmeans_k2_to_k8_and_hdbscan_parameter_grid():
    specs = build_experiment_specs("all")
    kmeans = [spec for spec in specs if spec["algorithm"] == "kmeans"]
    hdbscan = [spec for spec in specs if spec["algorithm"] == "hdbscan"]

    assert len(kmeans) == 14
    assert {spec["parameters"]["n_clusters"] for spec in kmeans} == set(range(2, 9))
    assert len(hdbscan) == 12
    assert {spec["parameters"]["min_cluster_size"] for spec in hdbscan} == {15, 30, 60}
    assert {spec["parameters"]["min_samples"] for spec in hdbscan} == {5, 15}
    assert all(spec["notes"]["min_samples_convention"] == "includes_the_point_itself" for spec in hdbscan)


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


def test_run_ids_are_stable_and_bind_the_feature_version():
    config = {"algorithm": "kmeans", "parameters": {"n_clusters": 2}}
    libraries = {"scikit-learn": "test"}

    assert run_id("features-a", config, libraries) == run_id("features-a", config, libraries)
    assert run_id("features-a", config, libraries) != run_id("features-b", config, libraries)


def seed_ml_dataset(db):
    with db.begin() as connection:
        import_id = connection.execute(text(
            "INSERT INTO data_imports (source_name, source_url, downloaded_at, data_version, license_info, "
            "record_count, import_status) VALUES ('SYNTHETIC', 'synthetic://ml', now(), 'SYNTHETIC-ml-v1', "
            "'SYNTHETIC', 12, 'completed') RETURNING id"
        )).scalar_one()
        boundary_id = connection.execute(text(
            "INSERT INTO city_boundary (import_id, name, source_type, source_id, geometry) VALUES "
            "(:import, 'SYNTHETIC Opole', 'relation', 987, "
            "ST_Multi(ST_Buffer(ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 5000))) "
            "RETURNING id"
        ), {"import": import_id}).scalar_one()
        grid_version = "g250-SYNTHETIC-ml-v1"
        feature_version = "f-SYNTHETIC-ml-v1"
        connection.execute(text(
            "INSERT INTO grid_versions (grid_version, boundary_id, cell_size_m, origin_x, origin_y, crs) "
            "VALUES (:grid, :boundary, 250, 0, 0, 'EPSG:2180')"
        ), {"grid": grid_version, "boundary": boundary_id})
        connection.execute(text(
            "INSERT INTO feature_versions (feature_version, grid_version, import_id, distance_method) "
            "VALUES (:feature, :grid, :import, 'euclidean')"
        ), {"feature": feature_version, "grid": grid_version, "import": import_id})

        for index in range(12):
            cell_id = f"{grid_version}-c{index:03d}-r000"
            x = 400000 + index * 250
            connection.execute(text(
                "INSERT INTO analysis_cells (id, grid_version, col, row, geometry, full_geometry, "
                "representative_point, representative_point_method, point_land_cover, area_m2, building_count, "
                "residential_area_m2, land_use_status, analysis_status) VALUES "
                "(:id, :grid, :col, 0, ST_Multi(ST_MakeEnvelope(:x, 560000, :x + 250, 560250, 2180)), "
                "ST_MakeEnvelope(:x, 560000, :x + 250, 560250, 2180), ST_SetSRID(ST_MakePoint(:x + 125, "
                "560125), 2180), 'centroid', 'ok', 62500, 1, 62500, 'potentially_residential', 'included')"
            ), {"id": cell_id, "grid": grid_version, "col": index, "x": x})
            values = [float(index % 2 * 100 + feature_index + 1) for feature_index in range(9)]
            columns = ", ".join(FEATURE_COLUMNS)
            placeholders = ", ".join(f":v{i}" for i in range(9))
            connection.execute(text(
                "INSERT INTO cell_features (cell_id, feature_version, " + columns +
                ", shop_status, school_status, bus_stop_status, buffer_sufficient) VALUES "
                "(:cell, :feature, " + placeholders + ", 'ok', 'ok', 'ok', true)"
            ), {"cell": cell_id, "feature": feature_version,
                **{f"v{i}": value for i, value in enumerate(values)}})
    return grid_version, feature_version


@pytest.mark.db
def test_pipeline_persists_versioned_run_assignments_and_is_idempotent(db):
    grid_version, feature_version = seed_ml_dataset(db)
    kwargs = {
        "grid_version": grid_version,
        "feature_version": feature_version,
        "algorithm": "kmeans",
        "scalers": ("standard",),
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
            "SELECT feature_version, grid_version, algorithm, random_state, n_samples, is_published, "
            "parameters->'validation_diagnostics' AS diagnostics "
            "FROM ml_runs WHERE id = :id"
        ), {"id": first["runs"][0]["id"]}).mappings().one()
        assignment_count = connection.execute(text(
            "SELECT count(*) FROM cluster_assignments WHERE run_id = :id"
        ), {"id": first["runs"][0]["id"]}).scalar_one()
        stats = connection.execute(text(
            "SELECT sum(cell_count), sum(sample_share) FROM cluster_statistics WHERE run_id = :id"
        ), {"id": first["runs"][0]["id"]}).one()

    assert run_row["feature_version"] == feature_version
    assert run_row["grid_version"] == grid_version
    assert run_row["algorithm"] == "kmeans"
    assert run_row["random_state"] == 42
    assert run_row["is_published"] is False
    assert run_row["diagnostics"]["rule"].startswith("Tukey 1.5*IQR")
    assert assignment_count == 12
    assert stats[0] == 12
    assert stats[1] == pytest.approx(1.0)


@pytest.mark.db
def test_pipeline_counts_cells_without_feature_rows_as_missing(db):
    grid_version, feature_version = seed_ml_dataset(db)
    with db.begin() as connection:
        connection.execute(text(
            "DELETE FROM cell_features WHERE cell_id = ("
            "SELECT id FROM analysis_cells WHERE grid_version = :grid ORDER BY id LIMIT 1)"
        ), {"grid": grid_version})

    result = run(
        grid_version=grid_version,
        feature_version=feature_version,
        algorithm="kmeans",
        scalers=("standard",),
        k_values=(2,),
        engine=db,
    )

    assert result["sample_counts"]["grid_cells"] == 12
    assert result["sample_counts"]["missing_feature_excluded"] == 1
    assert result["sample_counts"]["n_samples"] == 11
