"""SYNTHETIC fixtures only: the OSM IDs, names and geometries here are fictitious."""

import pytest
from sqlalchemy import text


def seed_synthetic(db):
    with db.begin() as connection:
        import_id = connection.execute(text(
            "INSERT INTO data_imports (source_name, source_url, downloaded_at, data_version, license_info, "
            "record_count, import_status, validation_report) VALUES "
            "('SYNTHETIC', 'https://example.invalid/synthetic', now(), 'SYNTHETIC-api-v1', 'SYNTHETIC', "
            "2, 'completed', '{}'::jsonb) RETURNING id"
        )).scalar_one()
        boundary_id = connection.execute(text(
            "INSERT INTO city_boundary (import_id, name, source_type, source_id, geometry) VALUES "
            "(:import, 'SYNTHETIC Opole', 'relation', 999, "
            "ST_Multi(ST_Buffer(ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 5000))) "
            "RETURNING id"
        ), {"import": import_id}).scalar_one()
        connection.execute(text(
            "INSERT INTO grid_versions (grid_version, boundary_id, cell_size_m, origin_x, origin_y, crs, parameters) "
            "VALUES ('g250-SYNTHETIC-api-v1', :boundary, 250, 0, 0, 'EPSG:2180', '{}'::jsonb)"
        ), {"boundary": boundary_id})
        connection.execute(text(
            "INSERT INTO services (import_id, source_type, source_id, name, category, subcategory, geometry, "
            "source_tags, inside_city) VALUES "
            "(:import, 'node', 101, 'SYNTHETIC sklep', 'shop', 'supermarket', "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), '{\"shop\":\"supermarket\"}'::jsonb, true), "
            "(:import, 'way', 202, NULL, 'school', 'primary', "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.925, 50.675), 4326), 2180), '{\"amenity\":\"school\"}'::jsonb, false)"
        ), {"import": import_id})
        connection.execute(text(
            "INSERT INTO analysis_cells (id, grid_version, col, row, geometry, full_geometry, representative_point, "
            "representative_point_method, point_land_cover, area_m2, building_count, residential_area_m2, "
            "land_use_status, analysis_status) VALUES ('g250-SYNTHETIC-api-v1-c000-r000', 'g250-SYNTHETIC-api-v1', "
            "0, 0, ST_Multi(ST_Buffer(ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 125)), "
            "ST_MakeEnvelope(0, 0, 250, 250, 2180), "
            "ST_Transform(ST_SetSRID(ST_MakePoint(17.92, 50.67), 4326), 2180), 'centroid', 'ok', 49087, 1, 100, "
            "'potentially_residential', 'included')"
        ))
        connection.execute(text(
            "INSERT INTO feature_versions (feature_version, grid_version, import_id, distance_method, parameters) "
            "VALUES ('f-SYNTHETIC-api-v1', 'g250-SYNTHETIC-api-v1', :import, 'euclidean', '{}'::jsonb)"
        ), {"import": import_id})
        connection.execute(text(
            "INSERT INTO cell_features (cell_id, feature_version, nearest_shop_m, nearest_school_m, nearest_bus_stop_m, "
            "shops_500m, shops_1000m, schools_500m, schools_1000m, bus_stops_500m, bus_stops_1000m, "
            "shop_status, school_status, bus_stop_status, buffer_sufficient) VALUES "
            "('g250-SYNTHETIC-api-v1-c000-r000', 'f-SYNTHETIC-api-v1', 0, 400, NULL, 1, 1, 1, 1, 0, 0, "
            "'ok', 'ok', 'none_found', false)"
        ))


@pytest.mark.db
def test_metadata_has_empty_lists_when_pipeline_has_not_written_data(db_client):
    response = db_client.get("/api/metadata")
    assert response.status_code == 200
    body = response.json()
    assert body["attribution_url"] == "https://www.openstreetmap.org/copyright"
    assert body["imports"] == []
    assert body["grid_versions"] == []
    assert body["feature_versions"] == []
    assert body["assessment_versions"] == []


@pytest.mark.db
def test_boundary_and_service_geojson_are_wgs84_and_include_osm_source(db, db_client):
    seed_synthetic(db)

    boundary = db_client.get("/api/boundary")
    assert boundary.status_code == 200
    boundary_feature = boundary.json()
    assert boundary_feature["type"] == "Feature"
    assert boundary_feature["properties"]["data_version"] == "SYNTHETIC-api-v1"
    assert boundary_feature["geometry"]["type"] == "MultiPolygon"

    services = db_client.get("/api/services", params={"category": "shop"})
    assert services.status_code == 200
    body = services.json()
    assert body["total"] == 1
    assert body["features"][0]["geometry"]["type"] == "Point"
    assert body["features"][0]["properties"]["source_url"] == "https://www.openstreetmap.org/node/101"
    assert body["features"][0]["properties"]["tags"] == {"shop": "supermarket"}
    coordinates = body["features"][0]["geometry"]["coordinates"]
    assert coordinates[0] == pytest.approx(17.92, abs=0.001)
    assert coordinates[1] == pytest.approx(50.67, abs=0.001)


@pytest.mark.db
def test_services_pagination_bbox_and_invalid_bbox(db, db_client):
    seed_synthetic(db)

    first = db_client.get("/api/services", params={"limit": 1})
    assert first.status_code == 200
    assert first.json()["total"] == 2
    assert len(first.json()["features"]) == 1
    second = db_client.get("/api/services", params={"limit": 1, "offset": 1})
    assert second.json()["features"][0]["properties"]["category"] != first.json()["features"][0]["properties"]["category"]

    filtered = db_client.get("/api/services", params={"bbox": "17.919,50.669,17.921,50.671"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1

    invalid = db_client.get("/api/services", params={"bbox": "18,51,17,50"})
    assert invalid.status_code == 422


@pytest.mark.db
def test_cells_return_versions_features_and_cell_detail(db, db_client):
    seed_synthetic(db)

    response = db_client.get("/api/cells", params={"limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["grid_version"] == "g250-SYNTHETIC-api-v1"
    assert body["feature_version"] == "f-SYNTHETIC-api-v1"
    assert body["total"] == 1
    properties = body["features"][0]["properties"]
    assert properties["features"]["nearest_shop_m"] == 0
    assert properties["features"]["bus_stops_500m"] == 0
    assert properties["features"]["nearest_bus_stop_m"] is None
    assert properties["cluster_label"] is None
    assert body["features"][0]["geometry"]["type"] == "MultiPolygon"

    detail = db_client.get("/api/cells/g250-SYNTHETIC-api-v1-c000-r000")
    assert detail.status_code == 200
    detail_properties = detail.json()["properties"]
    assert detail_properties["representative_point"] == pytest.approx([17.92, 50.67], abs=0.001)
    assert detail_properties["features"]["statuses"]["bus_stop"] == "none_found"
    assert detail_properties["features"]["buffer_sufficient"] is False
    assert detail_properties["clustering"] == []
    assert detail_properties["assessment"] == []


@pytest.mark.db
def test_cells_validate_grid_and_cell_detail_returns_404(db_client):
    missing_grid = db_client.get("/api/cells", params={"grid_version": "missing"})
    assert missing_grid.status_code == 404
    missing_cell = db_client.get("/api/cells/unknown-cell")
    assert missing_cell.status_code == 404


def test_bbox_parser_accepts_valid_bounds_and_rejects_invalid_bounds():
    from fastapi import HTTPException

    from app.api.read import parse_bbox

    assert parse_bbox("17.7,50.5,18.1,50.8") == (17.7, 50.5, 18.1, 50.8)
    for value in ("1,2,3", "1,2,3,4,5", "1,2,nan,4", "181,0,182,1", "2,1,1,2"):
        with pytest.raises(HTTPException) as error:
            parse_bbox(value)
        assert error.value.status_code == 422
