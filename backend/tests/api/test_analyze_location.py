"""SYNTHETIC-only coverage for POST /api/analyze-location."""

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy import text

from app.database import get_connection
from app.main import app
from tests.api.test_read import seed_synthetic


def request_payload(**overrides):
    return {"latitude": 50.67, "longitude": 17.92, "distance_method": "euclidean",
            "radius_m": 1000, **overrides}


@pytest.mark.parametrize("payload", [
    {"longitude": 17.92},
    {"latitude": "north", "longitude": 17.92},
    {"latitude": 91, "longitude": 17.92},
    {"latitude": 50.67, "longitude": 181},
    {"latitude": 50.67, "longitude": 17.92, "distance_method": "network"},
    {"latitude": 50.67, "longitude": 17.92, "radius_m": 99},
    {"latitude": 50.67, "longitude": 17.92, "radius_m": 2001},
])
def test_analyze_location_rejects_invalid_payload(client, payload):
    response = client.post("/api/analyze-location", json=payload)
    assert response.status_code == 422


@pytest.mark.db
def test_analyze_location_computes_point_features_and_reports_missing_sources(db, db_client):
    seed_synthetic(db)

    response = db_client.post("/api/analyze-location", json=request_payload())

    assert response.status_code == 200, response.text
    result = response.json()
    assert "mode" not in result
    assert "inside_city" not in result
    assert result["grid_version"] == "g250-SYNTHETIC-api-v1"
    assert result["data_version"] == "SYNTHETIC-api-v1"
    assert result["cell_id"] == "g250-SYNTHETIC-api-v1-c000-r000"
    assert result["point_features"]["nearest_shop_m"] == pytest.approx(0, abs=1)
    assert result["point_features"]["shops_within_radius"] == 1
    assert result["point_features"]["schools_within_radius"] == 1
    assert result["point_features"]["nearest_bus_stop_m"] is None
    assert result["point_features"]["bus_stops_within_radius"] is None
    assert result["cell_features"]["bus_stops_500m"] == 0
    assert result["clustering"] == []
    assert result["assessment"] == []
    assert "no_source:bus_stop" in result["warnings"]
    assert "no_published_model" in result["warnings"]
    assert "no_assessment" in result["warnings"]


@pytest.mark.db
def test_analyze_location_uses_only_published_assignments_and_keeps_hdbscan_noise(db, db_client):
    seed_synthetic(db)
    cell_id = "g250-SYNTHETIC-api-v1-c000-r000"
    with db.begin() as connection:
        for run_id, algorithm, label, published in (
            ("SYNTHETIC-kmeans-unpublished", "kmeans", 0, False),
            ("SYNTHETIC-hdbscan-published", "hdbscan", -1, True),
        ):
            connection.execute(text(
                "INSERT INTO ml_runs (id, algorithm, experiment, parameters, feature_version, grid_version, "
                "sample_filter, feature_set, feature_names, transform, scaler_name, random_state, n_samples, "
                "n_clusters, noise_fraction, metrics, library_versions, is_published) VALUES "
                "(:id, :algorithm, 'SYNTHETIC', '{}'::jsonb, 'f-SYNTHETIC-api-v1', 'g250-SYNTHETIC-api-v1', "
                "'all', 'all', ARRAY['nearest_shop_m'], 'none', 'none', 1, 1, 0, 1, '{}'::jsonb, '{}'::jsonb, :published)"
            ), {"id": run_id, "algorithm": algorithm, "published": published})
            connection.execute(text(
                "INSERT INTO cluster_assignments (run_id, cell_id, cluster_label) VALUES (:run, :cell, :label)"
            ), {"run": run_id, "cell": cell_id, "label": label})
        connection.execute(text(
            "INSERT INTO assessment_versions (assessment_version, feature_version, variant, parameters, description) "
            "VALUES ('a-SYNTHETIC-api-v1', 'f-SYNTHETIC-api-v1', 'relative', '{}'::jsonb, 'SYNTHETIC test')"
        ))
        connection.execute(text(
            "INSERT INTO accessibility_results (cell_id, assessment_version, accessibility_score, "
            "accessibility_class, criteria_results) VALUES (:cell, 'a-SYNTHETIC-api-v1', 0.5, 'medium', '{}'::jsonb)"
        ), {"cell": cell_id})

    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["clustering"] == [{
        "run_id": "SYNTHETIC-hdbscan-published", "algorithm": "hdbscan", "cluster_label": -1, "is_noise": True,
    }]
    assert result["assessment"][0]["assessment_version"] == "a-SYNTHETIC-api-v1"
    assert result["assessment"][0]["accessibility_class"] == "medium"
    assert "no_published_model" not in result["warnings"]
    assert "no_assessment" not in result["warnings"]


@pytest.mark.db
def test_analyze_location_rejects_outside_city_and_unknown_grid(db, db_client):
    seed_synthetic(db)
    outside = db_client.post("/api/analyze-location", json=request_payload(latitude=51.0))
    assert outside.status_code == 422

    missing_grid = db_client.post("/api/analyze-location", json=request_payload(grid_version="missing"))
    assert missing_grid.status_code == 404


@pytest.mark.db
def test_analyze_location_accepts_a_point_on_city_boundary(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        longitude, latitude = connection.execute(text(
            "SELECT ST_X(ST_Transform(ST_PointN(ST_ExteriorRing(ST_GeometryN(b.geometry, 1)), 1), 4326)), "
            "ST_Y(ST_Transform(ST_PointN(ST_ExteriorRing(ST_GeometryN(b.geometry, 1)), 1), 4326)) "
            "FROM city_boundary b LIMIT 1"
        )).one()
        connection.execute(text(
            "UPDATE analysis_cells c SET geometry = ST_Multi(ST_Buffer("
            "ST_PointN(ST_ExteriorRing(ST_GeometryN(b.geometry, 1)), 1), 50)) "
            "FROM city_boundary b WHERE c.id = 'g250-SYNTHETIC-api-v1-c000-r000'"
        ))
    response = db_client.post("/api/analyze-location", json=request_payload(
        latitude=latitude, longitude=longitude))
    assert response.status_code == 200, response.text
    assert "inside_city" not in response.json()


@pytest.mark.db
def test_analyze_location_missing_cell_features_does_not_hide_point_features(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        connection.execute(text("DELETE FROM cell_features"))
        connection.execute(text(
            "UPDATE data_imports SET validation_report = jsonb_build_object('buffer_m', :buffer)"
        ), {"buffer": 100})
        connection.execute(text(
            "UPDATE city_boundary SET geometry = ST_Multi(ST_Buffer("
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 500))"
        ))

    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["point_features"]["nearest_shop_m"] == pytest.approx(0, abs=1)
    assert result["point_features"]["shops_within_radius"] is None
    assert all(value is None for value in result["cell_features"].values())
    assert "insufficient_buffer:shop" in result["warnings"]
    assert "no_cell_features" in result["warnings"]


@pytest.mark.db
def test_analyze_location_returns_404_when_grid_has_no_covering_cell(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        connection.execute(text("DELETE FROM analysis_cells"))
    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 404


def test_analyze_location_returns_503_when_database_is_unavailable(client):
    def unavailable_connection():
        raise OperationalError("SELECT 1", {}, RuntimeError("synthetic unavailable database"))
        yield  # Make this a dependency generator.

    app.dependency_overrides[get_connection] = unavailable_connection
    try:
        response = client.post("/api/analyze-location", json=request_payload())
    finally:
        app.dependency_overrides.pop(get_connection, None)
    assert response.status_code == 503
