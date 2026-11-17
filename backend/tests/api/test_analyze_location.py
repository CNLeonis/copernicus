"""SYNTHETIC-only coverage for POST /api/analyze-location."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import get_connection
from app.main import app
from tests.api.test_read import CELL_ID, seed_synthetic


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
    {"latitude": 50.67, "longitude": 17.92, "cell_size_m": 333},
])
def test_analyze_location_rejects_invalid_payload(client, payload):
    app.dependency_overrides[get_connection] = lambda: object()
    try:
        assert client.post("/api/analyze-location", json=payload).status_code == 422
    finally:
        app.dependency_overrides.pop(get_connection, None)


@pytest.mark.db
def test_analyze_location_computes_point_features_and_missing_sources(db, db_client):
    seed_synthetic(db)
    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["cell_id"] == CELL_ID
    assert result["cell_size_m"] == 250
    assert "data_version" not in result
    assert "feature_version" not in result
    assert result["point_features"]["nearest_shop_m"] == pytest.approx(0, abs=1)
    assert result["point_features"]["shops_within_radius"] == 1
    assert result["point_features"]["schools_within_radius"] == 1
    assert result["point_features"]["nearest_bus_stop_m"] is None
    assert result["point_features"]["bus_stops_within_radius"] is None
    assert result["cell_features"]["bus_stops_500m"] == 0
    assert result["clustering"] == []
    assert result["assessment"] is None
    assert "no_source:bus_stop" in result["warnings"]
    assert "no_published_model" in result["warnings"]
    assert "no_assessment" in result["warnings"]


@pytest.mark.db
def test_analyze_location_uses_only_published_assignments_and_keeps_noise(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        for run_id, algorithm, label, published, parameters in (
            ("SYNTHETIC-kmeans-unpublished", "kmeans", 0, False, '{}'),
            ("SYNTHETIC-kmeans-nine", "kmeans", 1, True, '{"n_clusters": 2}'),
            ("SYNTHETIC-kmeans-six", "kmeans", 0, True,
             '{"n_clusters": 2, "feature_set": "counts_six"}'),
            ("SYNTHETIC-hdbscan-published", "hdbscan", -1, True, '{}'),
        ):
            connection.execute(text(
                "INSERT INTO ml_runs (id, algorithm, parameters, cell_size_m, sample_filter, scaler_name, "
                "random_state, n_samples, n_clusters, noise_fraction, metrics, is_published) VALUES "
                "(:id, :algorithm, CAST(:parameters AS jsonb), 250, 'all', 'standard', "
                "1, 1, 0, 1, '{}'::jsonb, :published)"
            ), {"id": run_id, "algorithm": algorithm,
                "published": published, "parameters": parameters})
            connection.execute(text(
                "INSERT INTO cluster_assignments (run_id, cell_id, cluster_label) VALUES (:run, :cell, :label)"
            ), {"run": run_id, "cell": CELL_ID, "label": label})
        connection.execute(text(
            "INSERT INTO accessibility_results (cell_id, accessibility_score, accessibility_class, criteria_results) "
            "VALUES (:cell, 0.5, 'medium', '{}'::jsonb)"
        ), {"cell": CELL_ID})

    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["clustering"] == [
        {"run_id": "SYNTHETIC-kmeans-six", "algorithm": "kmeans",
         "cluster_label": 0, "is_noise": False},
        {"run_id": "SYNTHETIC-hdbscan-published", "algorithm": "hdbscan",
         "cluster_label": -1, "is_noise": True},
    ]
    assert db_client.get("/api/ml/runs/SYNTHETIC-kmeans-nine").status_code == 200
    assert result["assessment"]["accessibility_class"] == "medium"
    assert "no_published_model" not in result["warnings"]
    assert "no_assessment" not in result["warnings"]


@pytest.mark.db
def test_analyze_location_rejects_outside_city_or_missing_grid(db, db_client):
    seed_synthetic(db)
    assert db_client.post("/api/analyze-location", json=request_payload(latitude=51.0)).status_code == 422
    assert db_client.post("/api/analyze-location", json=request_payload(cell_size_m=500)).status_code == 404


@pytest.mark.db
def test_missing_cell_features_does_not_hide_point_features(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        connection.execute(text("DELETE FROM cell_features"))
    response = db_client.post("/api/analyze-location", json=request_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["point_features"]["nearest_shop_m"] == pytest.approx(0, abs=1)
    assert all(value is None for value in result["cell_features"].values())
    assert "no_cell_features" in result["warnings"]


@pytest.mark.db
def test_analyze_location_returns_404_without_covering_cell(db, db_client):
    seed_synthetic(db)
    with db.begin() as connection:
        connection.execute(text("DELETE FROM analysis_cells"))
    assert db_client.post("/api/analyze-location", json=request_payload()).status_code == 404


def test_analyze_location_returns_503_when_database_is_unavailable(client):
    def unavailable_connection():
        raise OperationalError("SELECT 1", {}, RuntimeError("synthetic unavailable database"))
        yield

    app.dependency_overrides[get_connection] = unavailable_connection
    try:
        response = client.post("/api/analyze-location", json=request_payload())
    finally:
        app.dependency_overrides.pop(get_connection, None)
    assert response.status_code == 503
