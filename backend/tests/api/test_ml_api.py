"""ML API tests use fully synthetic database fixtures only."""

import pytest
from sqlalchemy import text

from pipeline.ml import run
from tests.pipeline.test_ml import seed_ml_dataset

pytestmark = pytest.mark.db


def test_ml_read_endpoints_serve_versioned_runs_and_paginated_assignments(db, db_client):
    grid_version, feature_version = seed_ml_dataset(db)
    result = run(
        grid_version=grid_version,
        feature_version=feature_version,
        algorithm="kmeans",
        scalers=("standard",),
        k_values=(2,),
        engine=db,
    )
    run_id = result["runs"][0]["id"]

    response = db_client.get("/api/ml/runs", params={
        "grid_version": grid_version, "algorithm": "kmeans", "published": "false", "limit": 1,
    })
    assert response.status_code == 200
    listing = response.json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == run_id
    assert listing["items"][0]["feature_version"] == feature_version
    assert listing["items"][0]["data_version"] == "SYNTHETIC-ml-v1"

    detail = db_client.get(f"/api/ml/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["cluster_sizes"]
    assert detail.json()["library_versions"]["scikit-learn"]

    clusters = db_client.get(f"/api/ml/runs/{run_id}/clusters")
    assert clusters.status_code == 200
    assert clusters.json()["total"] == 2
    assert all(item["low_access_share"] is None for item in clusters.json()["items"])

    assignments = db_client.get(f"/api/ml/runs/{run_id}/assignments", params={"limit": 1})
    assert assignments.status_code == 200
    assert assignments.json()["total"] == 12
    assert len(assignments.json()["items"]) == 1
    assert "is_noise" in assignments.json()["items"][0]

    assert db_client.get("/api/ml/runs/unknown").status_code == 404
    assert db_client.get("/api/ml/runs", params={"algorithm": "dbscan"}).status_code == 422


def test_ml_cluster_route_reports_low_access_share_for_matching_assessment(db, db_client):
    grid_version, feature_version = seed_ml_dataset(db)
    result = run(
        grid_version=grid_version,
        feature_version=feature_version,
        algorithm="kmeans",
        scalers=("standard",),
        k_values=(2,),
        engine=db,
    )
    run_id = result["runs"][0]["id"]
    with db.begin() as connection:
        assessment = connection.execute(text(
            "INSERT INTO assessment_versions (assessment_version, feature_version, variant, parameters, description) "
            "VALUES ('a-SYNTHETIC-ml-v1', :feature, 'threshold', '{}'::jsonb, 'SYNTHETIC test') "
            "RETURNING assessment_version"
        ), {"feature": feature_version}).scalar_one()
        first_cell = connection.execute(text(
            "SELECT cell_id FROM cluster_assignments WHERE run_id = :run ORDER BY cell_id LIMIT 1"
        ), {"run": run_id}).scalar_one()
        connection.execute(text(
            "INSERT INTO accessibility_results (cell_id, assessment_version, accessibility_class, criteria_results) "
            "VALUES (:cell, :assessment, 'low', '{}'::jsonb)"
        ), {"cell": first_cell, "assessment": assessment})

    response = db_client.get(f"/api/ml/runs/{run_id}/clusters", params={"assessment_version": assessment})
    assert response.status_code == 200
    shares = [item["low_access_share"] for item in response.json()["items"]]
    assert sorted(value for value in shares if value is not None) == [1.0]
    assert db_client.get(f"/api/ml/runs/{run_id}/clusters", params={"assessment_version": "missing"}).status_code == 404
