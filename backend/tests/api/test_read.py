"""All objects in these API fixtures are synthetic."""

import pytest
from sqlalchemy import text


CELL_ID = "g250-c000-r000"


def seed_synthetic(db):
    with db.begin() as connection:
        connection.execute(text(
            "INSERT INTO city_boundary (id, name, geometry) VALUES "
            "(1, 'SYNTHETIC Opole', ST_Multi(ST_Buffer("
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 5000)))"
        ))
        connection.execute(text(
            "INSERT INTO services (name, category, subcategory, geometry, inside_city) VALUES "
            "('SYNTHETIC sklep', 'shop', 'supermarket', "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), true), "
            "(NULL, 'school', 'primary', "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.925, 50.675), 4326), 2180), false)"
        ))
        connection.execute(text(
            "INSERT INTO analysis_cells (id, cell_size_m, col, row, geometry, full_geometry, representative_point, "
            "representative_point_method, point_land_cover, area_m2, building_count, residential_area_m2, "
            "land_use_status, analysis_status) VALUES "
            "('g250-c000-r000', 250, 0, 0, "
            "ST_Multi(ST_Buffer(ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 125)), "
            "ST_MakeEnvelope(0, 0, 250, 250, 2180), "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), "
            "'centroid', 'ok', 49087, 1, 100, 'potentially_residential', 'included')"
        ))
        connection.execute(text(
            "INSERT INTO cell_features (cell_id, nearest_shop_m, nearest_school_m, nearest_bus_stop_m, "
            "shops_500m, shops_1000m, schools_500m, schools_1000m, bus_stops_500m, bus_stops_1000m, "
            "shop_status, school_status, bus_stop_status, buffer_sufficient) VALUES "
            "('g250-c000-r000', 0, 400, NULL, 1, 1, 1, 1, 0, 0, 'ok', 'ok', 'none_found', false)"
        ))


def test_metadata_endpoint_is_removed(client):
    assert client.get("/api/metadata").status_code == 404


@pytest.mark.db
def test_boundary_and_services_have_only_analysis_fields(db, db_client):
    seed_synthetic(db)
    boundary = db_client.get("/api/boundary")
    assert boundary.status_code == 200
    assert boundary.json()["properties"] == {"name": "SYNTHETIC Opole"}
    assert boundary.json()["geometry"]["type"] == "MultiPolygon"

    response = db_client.get("/api/services", params={"category": "shop"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    properties = body["features"][0]["properties"]
    assert properties["category"] == "shop"
    assert "source_url" not in properties
    assert "tags" not in properties
    assert body["features"][0]["geometry"]["coordinates"] == pytest.approx([17.92, 50.67], abs=0.001)


@pytest.mark.db
def test_services_bbox_and_pagination(db, db_client):
    seed_synthetic(db)
    first = db_client.get("/api/services", params={"limit": 1})
    second = db_client.get("/api/services", params={"limit": 1, "offset": 1})
    assert first.json()["total"] == 2
    assert len(first.json()["features"]) == 1
    assert second.json()["features"][0]["properties"]["category"] != first.json()["features"][0]["properties"]["category"]
    assert db_client.get("/api/services", params={"bbox": "17.919,50.669,17.921,50.671"}).json()["total"] == 1
    assert db_client.get("/api/services", params={"bbox": "18,51,17,50"}).status_code == 422


@pytest.mark.db
def test_cells_and_details_use_one_current_feature_set(db, db_client):
    seed_synthetic(db)
    response = db_client.get("/api/cells", params={"cell_size_m": 250})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert "feature_version" not in body
    properties = body["features"][0]["properties"]
    assert properties["features"]["nearest_shop_m"] == 0
    assert properties["features"]["nearest_bus_stop_m"] is None
    assert properties["cluster_label"] is None

    detail = db_client.get(f"/api/cells/{CELL_ID}")
    assert detail.status_code == 200
    item = detail.json()["properties"]
    assert item["cell_size_m"] == 250
    assert item["representative_point"] == pytest.approx([17.92, 50.67], abs=0.001)
    assert item["features"]["statuses"]["bus_stop"] == "none_found"
    assert item["clustering"] == []
    assert item["assessment"] is None
    assert db_client.get("/api/cells/unknown-cell").status_code == 404


def test_bbox_parser_rejects_invalid_values():
    from fastapi import HTTPException
    from app.api.read import parse_bbox

    assert parse_bbox("17.7,50.5,18.1,50.8") == (17.7, 50.5, 18.1, 50.8)
    for value in ("1,2,3", "1,2,3,4,5", "1,2,nan,4", "181,0,182,1", "2,1,1,2"):
        with pytest.raises(HTTPException) as error:
            parse_bbox(value)
        assert error.value.status_code == 422
