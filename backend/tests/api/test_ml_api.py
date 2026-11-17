"""ML API tests use synthetic database fixtures."""

import pytest
from sqlalchemy import text

from pipeline.ml import run
from tests.pipeline.test_ml import seed_ml_dataset

pytestmark = pytest.mark.db


def seed_run(db):
    size = seed_ml_dataset(db)
    result = run(cell_size_m=size, algorithm="kmeans", k_values=(2,),
                 feature_set="all_nine", engine=db)
    return size, result["runs"][0]["id"]


def test_ml_read_endpoints_show_runs_and_assignments(db, db_client):
    size, run_id = seed_run(db)
    response = db_client.get("/api/ml/runs", params={
        "cell_size_m": size, "algorithm": "kmeans", "published": "false", "limit": 1,
    })
    assert response.status_code == 200
    listing = response.json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == run_id
    assert "feature_version" not in listing["items"][0]
    assert "data_version" not in listing["items"][0]

    detail = db_client.get(f"/api/ml/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["cluster_sizes"]
    assert "library_versions" not in detail.json()

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


def test_cluster_route_reports_low_access_share(db, db_client):
    _, run_id = seed_run(db)
    with db.begin() as connection:
        first_cell = connection.execute(text(
            "SELECT cell_id FROM cluster_assignments WHERE run_id = :run ORDER BY cell_id LIMIT 1"
        ), {"run": run_id}).scalar_one()
        connection.execute(text(
            "INSERT INTO accessibility_results (cell_id, accessibility_class, criteria_results) "
            "VALUES (:cell, 'low', '{}'::jsonb)"
        ), {"cell": first_cell})

    response = db_client.get(f"/api/ml/runs/{run_id}/clusters")
    assert response.status_code == 200
    shares = [item["low_access_share"] for item in response.json()["items"]]
    assert sorted(value for value in shares if value is not None) == [1.0]


def test_cells_default_to_published_six_count_run_and_keep_legacy_selectable(db, db_client):
    size, legacy_id = seed_run(db)
    default_id = run(cell_size_m=size, engine=db)["runs"][0]["id"]

    listing = db_client.get("/api/ml/runs", params={
        "cell_size_m": size, "algorithm": "kmeans", "published": "true", "limit": 1,
    })
    default = db_client.get("/api/cells", params={"cell_size_m": size, "limit": 1})
    legacy = db_client.get("/api/cells", params={
        "cell_size_m": size, "run_id": legacy_id, "limit": 1,
    })

    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == default_id
    assert listing.json()["items"][0]["parameters"]["feature_set"] == "counts_six"
    assert default.status_code == 200
    assert default.json()["run_id"] == default_id
    assert default.json()["features"][0]["properties"]["cluster_label"] is not None
    assert legacy.status_code == 200
    assert legacy.json()["run_id"] == legacy_id
