"""Read-only endpoints for the imported OSM and analysis datasets."""

import json
import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.core.config import CATEGORIES, FEATURE_COLUMNS, OSM_ATTRIBUTION, OSM_COPYRIGHT_URL
from app.database import get_connection

router = APIRouter(prefix="/api")
ConnectionDep = Annotated[Connection, Depends(get_connection)]

SERVICE_LIMIT = 5000
CELL_LIMIT = 10000
ML_RUN_LIMIT = 500


class AnalyzeLocationRequest(BaseModel):
    """Validated point-analysis request; analysis only reads stored ML results."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    distance_method: Literal["euclidean"] = "euclidean"
    radius_m: int = Field(default=1000, ge=100, le=2000)
    grid_version: str | None = None


@router.post("/analyze-location")
def analyze_location(payload: AnalyzeLocationRequest, connection: ConnectionDep) -> dict:
    """Compute service access at a point and read cell-level published results."""
    grid = connection.execute(text(
        "SELECT g.grid_version, d.id AS import_id, d.data_version, "
        "COALESCE(NULLIF(d.validation_report->>'buffer_m', '')::double precision, 0) AS buffer_m, "
        "b.id AS boundary_id "
        "FROM grid_versions g JOIN city_boundary b ON b.id = g.boundary_id "
        "JOIN data_imports d ON d.id = b.import_id "
        "WHERE (CAST(:version AS text) IS NULL OR g.grid_version = :version) "
        "AND d.import_status = 'completed' "
        "ORDER BY CASE WHEN CAST(:version AS text) IS NOT NULL THEN 0 "
        "ELSE CASE WHEN g.cell_size_m = 250 THEN 0 ELSE 1 END END, g.created_at DESC LIMIT 1"
    ), {"version": payload.grid_version}).mappings().first()
    if grid is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zapisanej siatki.")

    location = connection.execute(text(
        "SELECT (ST_Covers(b.geometry, p.geom) OR "
        "ST_DWithin(ST_Boundary(b.geometry), p.geom, 0.02)) AS inside_city, "
        "ST_Distance(ST_Boundary(b.geometry), p.geom) AS boundary_distance_m "
        "FROM city_boundary b CROSS JOIN "
        "(SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180) AS geom) p "
        "WHERE b.id = :boundary_id"
    ), {"lon": payload.longitude, "lat": payload.latitude,
        "boundary_id": grid["boundary_id"]}).mappings().first()
    if not location["inside_city"]:
        raise HTTPException(status_code=422, detail="Punkt znajduje się poza granicą Opola.")

    cell = connection.execute(text(
        "WITH p AS (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180) AS geom) "
        "SELECT c.id FROM analysis_cells c CROSS JOIN p "
        "WHERE c.grid_version = :grid AND (ST_Covers(c.geometry, p.geom) OR ST_DWithin(c.geometry, p.geom, 0.02)) "
        "ORDER BY c.id LIMIT 1"
    ), {"grid": grid["grid_version"], "lon": payload.longitude, "lat": payload.latitude}).mappings().first()
    if cell is None:
        raise HTTPException(status_code=404, detail="Punkt nie należy do komórki wybranej siatki.")

    feature_version = connection.execute(text(
        "SELECT feature_version FROM feature_versions WHERE grid_version = :grid AND import_id = :import "
        "ORDER BY computed_at DESC LIMIT 1"
    ), {"grid": grid["grid_version"], "import": grid["import_id"]}).scalar()
    feature_row = None
    if feature_version is not None:
        feature_row = connection.execute(text(
            "SELECT " + ", ".join(FEATURE_COLUMNS) + ", shop_status, school_status, bus_stop_status, buffer_sufficient "
            "FROM cell_features WHERE cell_id = :cell AND feature_version = :feature"
        ), {"cell": cell["id"], "feature": feature_version}).mappings().first()

    # The source import covers the city plus its documented buffer. For an interior
    # point, the distance to the city boundary adds to that guaranteed radius.
    coverage_m = float(grid["buffer_m"]) + float(location["boundary_distance_m"])
    point_features: dict[str, float | int | None] = {}
    warnings: list[str] = []
    categories = (("shop", "shops_within_radius"), ("school", "schools_within_radius"),
                  ("bus_stop", "bus_stops_within_radius"))
    for category, count_key in categories:
        nearest_key = f"nearest_{category}_m"
        source_count = connection.execute(text(
            "SELECT count(*) FROM services WHERE import_id = :import AND category = :category"
        ), {"import": grid["import_id"], "category": category}).scalar_one()
        if source_count == 0:
            point_features[nearest_key] = None
            point_features[count_key] = None
            warnings.append(f"no_source:{category}")
            continue

        nearest = connection.execute(text(
            "SELECT ST_Distance(s.geometry, p.geom) AS distance_m FROM services s "
            "CROSS JOIN (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180) AS geom) p "
            "WHERE s.import_id = :import AND s.category = :category "
            "ORDER BY s.geometry <-> p.geom LIMIT 1"
        ), {"import": grid["import_id"], "category": category,
            "lon": payload.longitude, "lat": payload.latitude}).mappings().first()
        nearest_distance = float(nearest["distance_m"])
        point_features[nearest_key] = nearest_distance if nearest_distance <= coverage_m else None
        if nearest_distance > coverage_m:
            warnings.append(f"nearest_distance_uncovered:{category}")

        if coverage_m < payload.radius_m:
            point_features[count_key] = None
            warning = f"insufficient_buffer:{category}"
            if warning not in warnings:
                warnings.append(warning)
        else:
            point_features[count_key] = connection.execute(text(
                "SELECT count(*)::integer FROM services s "
                "CROSS JOIN (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180) AS geom) p "
                "WHERE s.import_id = :import AND s.category = :category "
                "AND ST_DWithin(s.geometry, p.geom, :radius)"
            ), {"import": grid["import_id"], "category": category,
                "lon": payload.longitude, "lat": payload.latitude, "radius": payload.radius_m}).scalar_one()

    if feature_version is None:
        warnings.append("no_feature_version")
    elif feature_row is None:
        warnings.append("no_cell_features")
    else:
        for category, _ in categories:
            status = feature_row[f"{category}_status"]
            if status in ("no_source", "error"):
                warnings.append(f"cell_feature_{status}:{category}")
        if not feature_row["buffer_sufficient"]:
            warnings.append("cell_feature_buffer_insufficient")

    assignments = connection.execute(text(
        "SELECT r.id AS run_id, r.algorithm, a.cluster_label, (a.cluster_label = -1) AS is_noise "
        "FROM cluster_assignments a JOIN ml_runs r ON r.id = a.run_id "
        "WHERE a.cell_id = :cell AND r.grid_version = :grid AND r.is_published "
        "ORDER BY r.created_at DESC, r.id"
    ), {"cell": cell["id"], "grid": grid["grid_version"]}).mappings().all()
    if not assignments:
        warnings.append("no_published_model")

    assessments = connection.execute(text(
        "SELECT av.assessment_version, av.variant, ar.accessibility_class, ar.accessibility_score, "
        "ar.criteria_results FROM accessibility_results ar "
        "JOIN assessment_versions av USING (assessment_version) "
        "WHERE ar.cell_id = :cell AND av.feature_version = :feature "
        "ORDER BY av.created_at DESC, av.assessment_version"
    ), {"cell": cell["id"], "feature": feature_version}).mappings().all() if feature_version else []
    if not assessments:
        warnings.append("no_assessment")

    return {
        "location": {"latitude": payload.latitude, "longitude": payload.longitude},
        "cell_id": cell["id"],
        "grid_version": grid["grid_version"],
        "distance_method": payload.distance_method,
        "radius_m": payload.radius_m,
        "point_features": point_features,
        "cell_features": ({name: feature_row[name] for name in FEATURE_COLUMNS} if feature_row else
                          {name: None for name in FEATURE_COLUMNS}),
        "clustering": [dict(row) for row in assignments],
        "assessment": [dict(row) for row in assessments],
        "data_version": grid["data_version"],
        "feature_version": feature_version,
        "warnings": warnings,
    }


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        parts = [float(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise HTTPException(status_code=422, detail="bbox musi mieć format minLon,minLat,maxLon,maxLat.") from error
    if len(parts) != 4 or not all(math.isfinite(part) for part in parts):
        raise HTTPException(status_code=422, detail="bbox musi zawierać cztery skończone liczby.")
    west, south, east, north = parts
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90):
        raise HTTPException(status_code=422, detail="Współrzędne bbox są poza zakresem EPSG:4326.")
    if west >= east or south >= north:
        raise HTTPException(status_code=422, detail="Granice bbox muszą być rosnące.")
    return west, south, east, north


def _geojson(value: str | dict) -> dict:
    return value if isinstance(value, dict) else json.loads(value)


def _latest_grid(connection: Connection, grid_version: str | None) -> dict:
    row = connection.execute(text(
        "SELECT g.grid_version, g.cell_size_m, g.crs, g.parameters, g.created_at, d.data_version, "
        "g.boundary_id, d.id AS import_id FROM grid_versions g "
        "JOIN city_boundary b ON b.id = g.boundary_id JOIN data_imports d ON d.id = b.import_id "
        "WHERE (CAST(:version AS text) IS NULL OR g.grid_version = :version) "
        "ORDER BY CASE WHEN CAST(:version AS text) IS NOT NULL THEN 0 ELSE CASE WHEN g.cell_size_m = 250 THEN 0 ELSE 1 END END, "
        "g.created_at DESC LIMIT 1"
    ), {"version": grid_version}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zapisanej siatki.")
    return dict(row)


def _latest_feature(connection: Connection, grid_version: str, feature_version: str | None) -> str | None:
    if feature_version is not None:
        row = connection.execute(text(
            "SELECT feature_version FROM feature_versions WHERE feature_version = :version AND grid_version = :grid"
        ), {"version": feature_version, "grid": grid_version}).scalar()
        if row is None:
            raise HTTPException(status_code=404, detail="Nie znaleziono wersji cech dla wybranej siatki.")
        return row
    return connection.execute(text(
        "SELECT feature_version FROM feature_versions WHERE grid_version = :grid ORDER BY computed_at DESC LIMIT 1"
    ), {"grid": grid_version}).scalar()


def _validate_run(connection: Connection, run_id: str | None, grid_version: str) -> str | None:
    if run_id is None:
        return None
    row = connection.execute(text(
        "SELECT id FROM ml_runs WHERE id = :run AND grid_version = :grid"
    ), {"run": run_id, "grid": grid_version}).scalar()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono opublikowanego modelu dla wybranej siatki.")
    return row


def _validate_assessment(connection: Connection, assessment_version: str | None, feature_version: str | None) -> str | None:
    if assessment_version is None:
        return None
    if feature_version is None:
        raise HTTPException(status_code=404, detail="Ocena nie ma dostępnej wersji cech.")
    row = connection.execute(text(
        "SELECT assessment_version FROM assessment_versions WHERE assessment_version = :assessment "
        "AND feature_version = :feature"
    ), {"assessment": assessment_version, "feature": feature_version}).scalar()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono oceny dla wybranej wersji cech.")
    return row


@router.get("/metadata")
def metadata(connection: ConnectionDep) -> dict:
    imports = connection.execute(text(
        "SELECT data_version, source_name, source_url, downloaded_at, license_info, record_count, import_status, "
        "validation_report FROM data_imports ORDER BY downloaded_at DESC"
    )).mappings().all()
    grids = connection.execute(text(
        "SELECT g.grid_version, g.cell_size_m, g.crs, g.created_at, g.parameters, count(c.id)::integer AS cell_count "
        "FROM grid_versions g LEFT JOIN analysis_cells c USING (grid_version) "
        "GROUP BY g.grid_version ORDER BY g.created_at DESC"
    )).mappings().all()
    # Join through the grid boundary so every feature version reports the exact source import.
    features = connection.execute(text(
        "SELECT f.feature_version, f.grid_version, d.data_version, f.distance_method, f.parameters, f.computed_at "
        "FROM feature_versions f JOIN grid_versions g USING (grid_version) "
        "JOIN city_boundary b ON b.id = g.boundary_id JOIN data_imports d ON d.id = b.import_id "
        "ORDER BY f.computed_at DESC"
    )).mappings().all()
    assessments = connection.execute(text(
        "SELECT assessment_version, feature_version, variant, description, parameters "
        "FROM assessment_versions ORDER BY created_at DESC"
    )).mappings().all()
    return {
        "attribution": OSM_ATTRIBUTION,
        "attribution_url": OSM_COPYRIGHT_URL,
        "imports": [dict(row) for row in imports],
        "grid_versions": [dict(row) for row in grids],
        "feature_versions": [dict(row) for row in features],
        "assessment_versions": [dict(row) for row in assessments],
    }


@router.get("/boundary")
def boundary(connection: ConnectionDep) -> dict:
    row = connection.execute(text(
        "SELECT b.name, b.source_type, b.source_id, d.data_version, "
        "ST_AsGeoJSON(ST_Transform(b.geometry, 4326)) AS geometry "
        "FROM city_boundary b JOIN data_imports d ON d.id = b.import_id "
        "WHERE d.import_status = 'completed' ORDER BY d.downloaded_at DESC LIMIT 1"
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Granica Opola nie została zaimportowana.")
    return {
        "type": "Feature",
        "geometry": _geojson(row["geometry"]),
        "properties": {key: row[key] for key in ("name", "source_type", "source_id", "data_version")},
    }


@router.get("/services")
def services(
    connection: ConnectionDep,
    category: Annotated[list[str] | None, Query()] = None,
    bbox: str | None = None,
    limit: Annotated[int, Query(ge=1, le=SERVICE_LIMIT)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    categories = category or list(CATEGORIES)
    if any(item not in CATEGORIES for item in categories):
        raise HTTPException(status_code=422, detail=f"category musi należeć do: {', '.join(CATEGORIES)}.")
    bounds = parse_bbox(bbox)
    current_import = connection.execute(text(
        "SELECT id, data_version FROM data_imports WHERE import_status = 'completed' "
        "ORDER BY downloaded_at DESC LIMIT 1"
    )).mappings().first()
    if current_import is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zaimportowanych danych usług.")
    params: dict = {"import_id": current_import["id"], "categories": categories, "limit": limit, "offset": offset}
    bbox_clause = ""
    if bounds:
        west, south, east, north = bounds
        params.update({"west": west, "south": south, "east": east, "north": north})
        bbox_clause = "AND ST_Intersects(s.geometry, ST_Transform(ST_MakeEnvelope(:west, :south, :east, :north, 4326), 2180))"
    base = "FROM services s WHERE s.import_id = :import_id AND s.category = ANY(:categories) " + bbox_clause
    total = connection.execute(text("SELECT count(*)::integer " + base), params).scalar_one()
    rows = connection.execute(text(
        "SELECT s.id, s.category, s.subcategory, s.name, s.source_type, s.source_id, s.inside_city, "
        "s.source_tags, ST_AsGeoJSON(ST_Transform(s.geometry, 4326)) AS geometry " + base +
        " ORDER BY s.category, s.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": _geojson(row["geometry"]), "properties": {
                "id": row["id"], "category": row["category"], "subcategory": row["subcategory"],
                "name": row["name"], "source_type": row["source_type"], "source_id": row["source_id"],
                "source_url": f"https://www.openstreetmap.org/{row['source_type']}/{row['source_id']}",
                "inside_city": row["inside_city"], "tags": row["source_tags"],
            }} for row in rows
        ],
        "total": total, "limit": limit, "offset": offset, "data_version": current_import["data_version"],
    }


@router.get("/cells")
def cells(
    connection: ConnectionDep,
    grid_version: str | None = None,
    bbox: str | None = None,
    feature_version: str | None = None,
    run_id: str | None = None,
    assessment_version: str | None = None,
    limit: Annotated[int, Query(ge=1, le=CELL_LIMIT)] = 5000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    bounds = parse_bbox(bbox)
    grid = _latest_grid(connection, grid_version)
    chosen_feature = _latest_feature(connection, grid["grid_version"], feature_version)
    chosen_run = _validate_run(connection, run_id, grid["grid_version"])
    chosen_assessment = _validate_assessment(connection, assessment_version, chosen_feature)
    params: dict = {
        "grid": grid["grid_version"], "feature": chosen_feature, "run": chosen_run,
        "assessment": chosen_assessment, "limit": limit, "offset": offset,
    }
    bbox_clause = ""
    if bounds:
        west, south, east, north = bounds
        params.update({"west": west, "south": south, "east": east, "north": north})
        bbox_clause = "AND ST_Intersects(c.geometry, ST_Transform(ST_MakeEnvelope(:west, :south, :east, :north, 4326), 2180))"
    joins = (
        "LEFT JOIN cell_features f ON f.cell_id = c.id AND f.feature_version = :feature "
        "LEFT JOIN cluster_assignments ca ON ca.cell_id = c.id AND ca.run_id = :run "
        "LEFT JOIN accessibility_results ar ON ar.cell_id = c.id AND ar.assessment_version = :assessment "
    )
    base = "FROM analysis_cells c " + joins + "WHERE c.grid_version = :grid " + bbox_clause
    total = connection.execute(text(
        "SELECT count(*)::integer FROM analysis_cells c WHERE c.grid_version = :grid " + bbox_clause
    ), params).scalar_one()
    rows = connection.execute(text(
        "SELECT c.id, c.area_m2, c.land_use_status, c.analysis_status, f.nearest_shop_m, f.nearest_school_m, "
        "f.nearest_bus_stop_m, f.shops_500m, f.shops_1000m, f.schools_500m, f.schools_1000m, "
        "f.bus_stops_500m, f.bus_stops_1000m, ca.cluster_label, (ca.cluster_label = -1) AS is_noise, "
        "ar.accessibility_class, ar.accessibility_score, "
        "ST_AsGeoJSON(ST_Transform(c.geometry, 4326)) AS geometry " + base +
        " ORDER BY c.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "geometry": _geojson(row["geometry"]), "properties": {
                "id": row["id"], "area_m2": row["area_m2"], "land_use_status": row["land_use_status"],
                "analysis_status": row["analysis_status"],
                "features": {name: row[name] for name in FEATURE_COLUMNS},
                "cluster_label": row["cluster_label"], "is_noise": row["is_noise"],
                "accessibility_class": row["accessibility_class"], "accessibility_score": row["accessibility_score"],
            }} for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "grid_version": grid["grid_version"], "feature_version": chosen_feature,
        "run_id": chosen_run, "assessment_version": chosen_assessment,
    }


@router.get("/cells/{cell_id}")
def cell_detail(
    cell_id: str,
    connection: ConnectionDep,
    feature_version: str | None = None,
) -> dict:
    row = connection.execute(text(
        "SELECT c.id, c.grid_version, c.area_m2, c.building_count, c.residential_area_m2, c.land_use_status, "
        "c.analysis_status, c.representative_point_method, c.point_land_cover, "
        "ST_AsGeoJSON(ST_Transform(c.geometry, 4326)) AS geometry, "
        "ST_X(ST_Transform(c.representative_point, 4326)) AS point_lon, "
        "ST_Y(ST_Transform(c.representative_point, 4326)) AS point_lat "
        "FROM analysis_cells c WHERE c.id = :id"
    ), {"id": cell_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono komórki.")
    chosen_feature = _latest_feature(connection, row["grid_version"], feature_version) if feature_version else _latest_feature(
        connection, row["grid_version"], None)
    feature_row = None
    if chosen_feature:
        feature_row = connection.execute(text(
            "SELECT * FROM cell_features WHERE cell_id = :cell AND feature_version = :version"
        ), {"cell": cell_id, "version": chosen_feature}).mappings().first()
    features = {name: feature_row[name] if feature_row else None for name in FEATURE_COLUMNS}
    statuses = {category: feature_row[f"{category}_status"] if feature_row else None
                for category in ("shop", "school", "bus_stop")}
    assignments = connection.execute(text(
        "SELECT r.id AS run_id, r.algorithm, a.cluster_label, (a.cluster_label = -1) AS is_noise "
        "FROM cluster_assignments a JOIN ml_runs r ON r.id = a.run_id "
        "WHERE a.cell_id = :cell AND r.is_published ORDER BY r.created_at DESC"
    ), {"cell": cell_id}).mappings().all()
    assessments = connection.execute(text(
        "SELECT av.assessment_version, av.variant, ar.accessibility_class, ar.accessibility_score, ar.criteria_results "
        "FROM accessibility_results ar JOIN assessment_versions av USING (assessment_version) "
        "WHERE ar.cell_id = :cell ORDER BY av.created_at DESC"
    ), {"cell": cell_id}).mappings().all()
    result_feature = {
        **features,
        "statuses": statuses,
        "buffer_sufficient": feature_row["buffer_sufficient"] if feature_row else None,
        "feature_version": chosen_feature,
    }
    return {
        "type": "Feature", "geometry": _geojson(row["geometry"]),
        "properties": {
            "id": row["id"], "grid_version": row["grid_version"], "area_m2": row["area_m2"],
            "building_count": row["building_count"], "residential_area_m2": row["residential_area_m2"],
            "land_use_status": row["land_use_status"], "analysis_status": row["analysis_status"],
            "representative_point": [row["point_lon"], row["point_lat"]],
            "representative_point_method": row["representative_point_method"],
            "point_land_cover": row["point_land_cover"], "features": result_feature,
            "clustering": [dict(item) for item in assignments],
            "assessment": [dict(item) for item in assessments],
        },
    }


@router.get("/ml/runs")
def ml_runs(
    connection: ConnectionDep,
    grid_version: str | None = None,
    algorithm: Literal["kmeans", "hdbscan"] | None = None,
    published: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=ML_RUN_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    clauses = [
        "(CAST(:grid AS text) IS NULL OR r.grid_version = :grid)",
        "(CAST(:algorithm AS text) IS NULL OR r.algorithm = :algorithm)",
        "(CAST(:published AS boolean) IS NULL OR r.is_published = :published)",
    ]
    params = {"grid": grid_version, "algorithm": algorithm, "published": published,
              "limit": limit, "offset": offset}
    where = " AND ".join(clauses)
    total = connection.execute(text(
        "SELECT count(*)::integer FROM ml_runs r WHERE " + where
    ), params).scalar_one()
    rows = connection.execute(text(
        "SELECT r.id, r.algorithm, r.experiment, r.parameters, r.feature_version, r.grid_version, "
        "d.data_version, r.sample_filter, r.feature_set, r.feature_names, r.transform, r.scaler_name, "
        "r.random_state, r.n_samples, r.n_clusters, r.noise_fraction, r.metrics, r.is_published, r.created_at "
        "FROM ml_runs r JOIN feature_versions f ON f.feature_version = r.feature_version "
        "JOIN data_imports d ON d.id = f.import_id WHERE " + where +
        " ORDER BY r.created_at DESC, r.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/ml/runs/{run_id}")
def ml_run_detail(run_id: str, connection: ConnectionDep) -> dict:
    row = connection.execute(text(
        "SELECT r.id, r.algorithm, r.experiment, r.parameters, r.feature_version, r.grid_version, "
        "d.data_version, r.sample_filter, r.feature_set, r.feature_names, r.transform, r.scaler_name, "
        "r.random_state, r.n_samples, r.n_clusters, r.noise_fraction, r.metrics, r.library_versions, "
        "r.is_published, r.artifact_path, r.created_at "
        "FROM ml_runs r JOIN feature_versions f ON f.feature_version = r.feature_version "
        "JOIN data_imports d ON d.id = f.import_id WHERE r.id = :id"
    ), {"id": run_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono przebiegu ML.")
    sizes = connection.execute(text(
        "SELECT cluster_label, cell_count FROM cluster_statistics WHERE run_id = :id ORDER BY cluster_label"
    ), {"id": run_id}).mappings().all()
    return {**dict(row), "cluster_sizes": [dict(item) for item in sizes]}


@router.get("/ml/runs/{run_id}/clusters")
def ml_run_clusters(
    run_id: str,
    connection: ConnectionDep,
    assessment_version: str | None = None,
) -> dict:
    run = connection.execute(text(
        "SELECT feature_version FROM ml_runs WHERE id = :id"
    ), {"id": run_id}).mappings().first()
    if run is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono przebiegu ML.")
    if assessment_version is not None:
        valid_assessment = connection.execute(text(
            "SELECT 1 FROM assessment_versions WHERE assessment_version = :assessment "
            "AND feature_version = :feature"
        ), {"assessment": assessment_version, "feature": run["feature_version"]}).scalar()
        if valid_assessment is None:
            raise HTTPException(status_code=404, detail="Nie znaleziono oceny dla wersji cech tego przebiegu.")
    rows = connection.execute(text(
        "WITH access_by_cluster AS ("
        " SELECT ca.cluster_label, count(ar.accessibility_class)::integer AS assessed_count, "
        " count(*) FILTER (WHERE ar.accessibility_class = 'low')::integer AS low_count "
        " FROM cluster_assignments ca LEFT JOIN accessibility_results ar "
        " ON ar.cell_id = ca.cell_id AND ar.assessment_version = CAST(:assessment AS text) "
        " WHERE ca.run_id = :id GROUP BY ca.cluster_label) "
        "SELECT cs.cluster_label, (cs.cluster_label = -1) AS is_noise, cs.cell_count, cs.area_m2, "
        "cs.sample_share, cs.feature_statistics, "
        "CASE WHEN CAST(:assessment AS text) IS NULL OR ac.assessed_count = 0 THEN NULL "
        "ELSE ac.low_count::double precision / ac.assessed_count END AS low_access_share "
        "FROM cluster_statistics cs LEFT JOIN access_by_cluster ac USING (cluster_label) "
        "WHERE cs.run_id = :id ORDER BY cs.cluster_label"
    ), {"id": run_id, "assessment": assessment_version}).mappings().all()
    return {"run_id": run_id, "assessment_version": assessment_version,
            "items": [dict(row) for row in rows], "total": len(rows)}


@router.get("/ml/runs/{run_id}/assignments")
def ml_run_assignments(
    run_id: str,
    connection: ConnectionDep,
    limit: Annotated[int, Query(ge=1, le=CELL_LIMIT)] = 5000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    exists = connection.execute(text("SELECT 1 FROM ml_runs WHERE id = :id"), {"id": run_id}).scalar()
    if exists is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono przebiegu ML.")
    total = connection.execute(text(
        "SELECT count(*)::integer FROM cluster_assignments WHERE run_id = :id"
    ), {"id": run_id}).scalar_one()
    rows = connection.execute(text(
        "SELECT cell_id, cluster_label, (cluster_label = -1) AS is_noise "
        "FROM cluster_assignments WHERE run_id = :id ORDER BY cell_id LIMIT :limit OFFSET :offset"
    ), {"id": run_id, "limit": limit, "offset": offset}).mappings().all()
    return {"run_id": run_id, "items": [dict(row) for row in rows],
            "total": total, "limit": limit, "offset": offset}
