"""Read the current services and results computed by the offline pipeline."""

import json
import math
from enum import IntEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.core.config import BOUNDARY_BUFFER_M, CATEGORIES, FEATURE_COLUMNS
from app.database import get_connection

router = APIRouter(prefix="/api")
ConnectionDep = Annotated[Connection, Depends(get_connection)]

SERVICE_LIMIT = 5000
CELL_LIMIT = 10000
ML_RUN_LIMIT = 500

POINT_SQL = "ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180)"


class CellSize(IntEnum):
    SMALL = 250
    LARGE = 500


class AnalyzeLocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    distance_method: Literal["euclidean"] = "euclidean"
    radius_m: int = Field(default=1000, ge=100, le=2000)
    cell_size_m: Literal[250, 500] = 250


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
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


def _bbox_clause(alias: str, bbox: str | None) -> tuple[str, dict]:
    bounds = parse_bbox(bbox)
    if bounds is None:
        return "", {}
    west, south, east, north = bounds
    return (
        f"AND ST_Intersects({alias}.geometry, "
        "ST_Transform(ST_MakeEnvelope(:west, :south, :east, :north, 4326), 2180))",
        {"west": west, "south": south, "east": east, "north": north},
    )


def _geojson(value: str | dict) -> dict:
    return value if isinstance(value, dict) else json.loads(value)


def _cell_features(connection: Connection, cell_id: str) -> dict | None:
    row = connection.execute(text(
        "SELECT " + ", ".join(FEATURE_COLUMNS) +
        ", shop_status, school_status, bus_stop_status, buffer_sufficient "
        "FROM cell_features WHERE cell_id = :id"
    ), {"id": cell_id}).mappings().first()
    return dict(row) if row else None


def _assignments(connection: Connection, cell_id: str, cell_size_m: int) -> list[dict]:
    rows = connection.execute(text(
        "SELECT r.id AS run_id, r.algorithm, a.cluster_label, "
        "(a.cluster_label = -1) AS is_noise "
        "FROM cluster_assignments a JOIN ml_runs r ON r.id = a.run_id "
        "WHERE a.cell_id = :cell AND r.cell_size_m = :size AND r.is_published "
        "ORDER BY CASE WHEN r.parameters->>'feature_set' = 'counts_six' "
        "AND r.algorithm = 'kmeans' AND r.parameters->>'n_clusters' = '2' "
        "THEN 0 ELSE 1 END, r.id"
    ), {"cell": cell_id, "size": cell_size_m}).mappings().all()
    return [dict(row) for row in rows]


def _assessment(connection: Connection, cell_id: str) -> dict | None:
    row = connection.execute(text(
        "SELECT accessibility_class, accessibility_score, criteria_results "
        "FROM accessibility_results WHERE cell_id = :cell"
    ), {"cell": cell_id}).mappings().first()
    return dict(row) if row else None


def _run(connection: Connection, run_id: str) -> dict:
    row = connection.execute(text(
        "SELECT id, algorithm, parameters, cell_size_m, sample_filter, scaler_name, "
        "random_state, n_samples, n_clusters, noise_fraction, metrics, is_published "
        "FROM ml_runs WHERE id = :id"
    ), {"id": run_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono przebiegu ML.")
    return dict(row)


def _default_run_id(connection: Connection, cell_size_m: int) -> str | None:
    return connection.execute(text(
        "SELECT id FROM ml_runs WHERE cell_size_m = :size AND is_published "
        "AND sample_filter = 'all' AND algorithm = 'kmeans' "
        "AND parameters->>'feature_set' = 'counts_six' "
        "AND parameters->>'n_clusters' = '2' ORDER BY id LIMIT 1"
    ), {"size": cell_size_m}).scalar()


@router.post("/analyze-location")
def analyze_location(payload: AnalyzeLocationRequest, connection: ConnectionDep) -> dict:
    """Compute point features; read the stored cell, model and rule results."""
    location = connection.execute(text(
        "SELECT (ST_Covers(b.geometry, p.geom) OR "
        "ST_DWithin(ST_Boundary(b.geometry), p.geom, 0.02)) AS inside_city, "
        "ST_Distance(ST_Boundary(b.geometry), p.geom) AS boundary_distance_m "
        "FROM city_boundary b CROSS JOIN (SELECT " + POINT_SQL + " AS geom) p "
        "WHERE b.id = 1"
    ), {"lon": payload.longitude, "lat": payload.latitude}).mappings().first()
    if location is None:
        raise HTTPException(status_code=404, detail="Granica Opola nie została zaimportowana.")
    if not location["inside_city"]:
        raise HTTPException(status_code=422, detail="Punkt znajduje się poza granicą Opola.")

    cell = connection.execute(text(
        "WITH p AS (SELECT " + POINT_SQL + " AS geom) "
        "SELECT c.id FROM analysis_cells c CROSS JOIN p "
        "WHERE c.cell_size_m = :size AND "
        "(ST_Covers(c.geometry, p.geom) OR ST_DWithin(c.geometry, p.geom, 0.02)) "
        "ORDER BY c.id LIMIT 1"
    ), {"size": payload.cell_size_m, "lon": payload.longitude,
        "lat": payload.latitude}).mappings().first()
    if cell is None:
        raise HTTPException(status_code=404, detail="Punkt nie należy do komórki wybranej siatki.")

    coverage_m = BOUNDARY_BUFFER_M + float(location["boundary_distance_m"])
    point_features: dict[str, float | int | None] = {}
    warnings: list[str] = []
    categories = (
        ("shop", "shops_within_radius"),
        ("school", "schools_within_radius"),
        ("bus_stop", "bus_stops_within_radius"),
    )
    for category, count_key in categories:
        nearest_key = f"nearest_{category}_m"
        nearest = connection.execute(text(
            "SELECT ST_Distance(s.geometry, p.geom) AS distance_m FROM services s "
            "CROSS JOIN (SELECT " + POINT_SQL + " AS geom) p "
            "WHERE s.category = :category ORDER BY s.geometry <-> p.geom LIMIT 1"
        ), {"category": category, "lon": payload.longitude,
            "lat": payload.latitude}).mappings().first()
        if nearest is None:
            point_features[nearest_key] = None
            point_features[count_key] = None
            warnings.append(f"no_source:{category}")
            continue

        nearest_distance = float(nearest["distance_m"])
        point_features[nearest_key] = nearest_distance if nearest_distance <= coverage_m else None
        if nearest_distance > coverage_m:
            warnings.append(f"nearest_distance_uncovered:{category}")
        if coverage_m < payload.radius_m:
            point_features[count_key] = None
            warnings.append(f"insufficient_buffer:{category}")
        else:
            point_features[count_key] = connection.execute(text(
                "SELECT count(*)::integer FROM services s "
                "CROSS JOIN (SELECT " + POINT_SQL + " AS geom) p "
                "WHERE s.category = :category AND ST_DWithin(s.geometry, p.geom, :radius)"
            ), {"category": category, "lon": payload.longitude,
                "lat": payload.latitude, "radius": payload.radius_m}).scalar_one()

    feature_row = _cell_features(connection, cell["id"])
    if feature_row is None:
        warnings.append("no_cell_features")
    else:
        for category in CATEGORIES:
            status = feature_row[f"{category}_status"]
            if status in ("no_source", "error"):
                warnings.append(f"cell_feature_{status}:{category}")
        if not feature_row["buffer_sufficient"]:
            warnings.append("cell_feature_buffer_insufficient")

    assignments = _assignments(connection, cell["id"], payload.cell_size_m)
    assessment = _assessment(connection, cell["id"])
    if not assignments:
        warnings.append("no_published_model")
    if assessment is None:
        warnings.append("no_assessment")

    return {
        "location": {"latitude": payload.latitude, "longitude": payload.longitude},
        "cell_id": cell["id"],
        "cell_size_m": payload.cell_size_m,
        "distance_method": payload.distance_method,
        "radius_m": payload.radius_m,
        "point_features": point_features,
        "cell_features": (
            {name: feature_row[name] for name in FEATURE_COLUMNS} if feature_row
            else {name: None for name in FEATURE_COLUMNS}
        ),
        "clustering": assignments,
        "assessment": assessment,
        "warnings": warnings,
    }


@router.get("/boundary")
def boundary(connection: ConnectionDep) -> dict:
    row = connection.execute(text(
        "SELECT name, ST_AsGeoJSON(ST_Transform(geometry, 4326)) AS geometry "
        "FROM city_boundary WHERE id = 1"
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Granica Opola nie została zaimportowana.")
    return {
        "type": "Feature",
        "geometry": _geojson(row["geometry"]),
        "properties": {"name": row["name"]},
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
    bbox_sql, bbox_params = _bbox_clause("s", bbox)
    params = {"categories": categories, "limit": limit, "offset": offset, **bbox_params}
    base = "FROM services s WHERE s.category = ANY(:categories) " + bbox_sql
    total = connection.execute(text("SELECT count(*)::integer " + base), params).scalar_one()
    rows = connection.execute(text(
        "SELECT s.id, s.category, s.subcategory, s.name, s.inside_city, "
        "ST_AsGeoJSON(ST_Transform(s.geometry, 4326)) AS geometry " + base +
        " ORDER BY s.category, s.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": _geojson(row["geometry"]),
            "properties": {
                "id": row["id"], "category": row["category"],
                "subcategory": row["subcategory"], "name": row["name"],
                "inside_city": row["inside_city"],
            },
        } for row in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/cells")
def cells(
    connection: ConnectionDep,
    cell_size_m: CellSize = CellSize.SMALL,
    bbox: str | None = None,
    run_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=CELL_LIMIT)] = 5000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    bbox_sql, bbox_params = _bbox_clause("c", bbox)
    if run_id is None:
        run_id = _default_run_id(connection, cell_size_m)
    if run_id is not None and _run(connection, run_id)["cell_size_m"] != cell_size_m:
        raise HTTPException(status_code=404, detail="Przebieg ML nie należy do wybranej siatki.")
    params = {"size": cell_size_m, "run": run_id, "limit": limit, "offset": offset, **bbox_params}
    base = "FROM analysis_cells c WHERE c.cell_size_m = :size " + bbox_sql
    total = connection.execute(text("SELECT count(*)::integer " + base), params).scalar_one()
    rows = connection.execute(text(
        "SELECT c.id, c.area_m2, c.land_use_status, c.analysis_status, " +
        ", ".join(f"f.{name}" for name in FEATURE_COLUMNS) +
        ", ca.cluster_label, (ca.cluster_label = -1) AS is_noise, "
        "ar.accessibility_class, ar.accessibility_score, "
        "ST_AsGeoJSON(ST_Transform(c.geometry, 4326)) AS geometry "
        "FROM analysis_cells c "
        "LEFT JOIN cell_features f ON f.cell_id = c.id "
        "LEFT JOIN cluster_assignments ca ON ca.cell_id = c.id AND ca.run_id = :run "
        "LEFT JOIN accessibility_results ar ON ar.cell_id = c.id "
        "WHERE c.cell_size_m = :size " + bbox_sql +
        " ORDER BY c.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": _geojson(row["geometry"]),
            "properties": {
                "id": row["id"], "area_m2": row["area_m2"],
                "land_use_status": row["land_use_status"],
                "analysis_status": row["analysis_status"],
                "features": {name: row[name] for name in FEATURE_COLUMNS},
                "cluster_label": row["cluster_label"], "is_noise": row["is_noise"],
                "accessibility_class": row["accessibility_class"],
                "accessibility_score": row["accessibility_score"],
            },
        } for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "cell_size_m": cell_size_m, "run_id": run_id,
    }


@router.get("/cells/{cell_id}")
def cell_detail(cell_id: str, connection: ConnectionDep) -> dict:
    row = connection.execute(text(
        "SELECT c.id, c.cell_size_m, c.area_m2, c.building_count, c.residential_area_m2, "
        "c.land_use_status, c.analysis_status, c.representative_point_method, c.point_land_cover, "
        "ST_AsGeoJSON(ST_Transform(c.geometry, 4326)) AS geometry, "
        "ST_X(ST_Transform(c.representative_point, 4326)) AS point_lon, "
        "ST_Y(ST_Transform(c.representative_point, 4326)) AS point_lat "
        "FROM analysis_cells c WHERE c.id = :id"
    ), {"id": cell_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono komórki.")
    feature_row = _cell_features(connection, cell_id)
    features = {
        **{name: feature_row[name] if feature_row else None for name in FEATURE_COLUMNS},
        "statuses": {
            category: feature_row[f"{category}_status"] if feature_row else None
            for category in CATEGORIES
        },
        "buffer_sufficient": feature_row["buffer_sufficient"] if feature_row else None,
    }
    return {
        "type": "Feature",
        "geometry": _geojson(row["geometry"]),
        "properties": {
            "id": row["id"], "cell_size_m": row["cell_size_m"],
            "area_m2": row["area_m2"], "building_count": row["building_count"],
            "residential_area_m2": row["residential_area_m2"],
            "land_use_status": row["land_use_status"],
            "analysis_status": row["analysis_status"],
            "representative_point": [row["point_lon"], row["point_lat"]],
            "representative_point_method": row["representative_point_method"],
            "point_land_cover": row["point_land_cover"],
            "features": features,
            "clustering": _assignments(connection, cell_id, row["cell_size_m"]),
            "assessment": _assessment(connection, cell_id),
        },
    }


@router.get("/ml/runs")
def ml_runs(
    connection: ConnectionDep,
    cell_size_m: CellSize | None = None,
    algorithm: Literal["kmeans", "hdbscan"] | None = None,
    published: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=ML_RUN_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    clauses = [
        "(CAST(:size AS integer) IS NULL OR r.cell_size_m = :size)",
        "(CAST(:algorithm AS text) IS NULL OR r.algorithm = :algorithm)",
        "(CAST(:published AS boolean) IS NULL OR r.is_published = :published)",
    ]
    params = {
        "size": cell_size_m, "algorithm": algorithm, "published": published,
        "limit": limit, "offset": offset,
    }
    where = " AND ".join(clauses)
    total = connection.execute(text(
        "SELECT count(*)::integer FROM ml_runs r WHERE " + where
    ), params).scalar_one()
    rows = connection.execute(text(
        "SELECT r.id, r.algorithm, r.parameters, r.cell_size_m, r.sample_filter, "
        "r.scaler_name, r.random_state, r.n_samples, r.n_clusters, r.noise_fraction, "
        "r.metrics, r.is_published "
        "FROM ml_runs r WHERE " + where +
        " ORDER BY CASE WHEN r.is_published AND r.algorithm = 'kmeans' "
        "AND r.parameters->>'feature_set' = 'counts_six' "
        "AND r.parameters->>'n_clusters' = '2' THEN 0 ELSE 1 END, "
        "r.cell_size_m, r.id LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/ml/runs/{run_id}")
def ml_run_detail(run_id: str, connection: ConnectionDep) -> dict:
    run = _run(connection, run_id)
    sizes = connection.execute(text(
        "SELECT cluster_label, cell_count FROM cluster_statistics "
        "WHERE run_id = :id ORDER BY cluster_label"
    ), {"id": run_id}).mappings().all()
    return {**run, "cluster_sizes": [dict(item) for item in sizes]}


@router.get("/ml/runs/{run_id}/clusters")
def ml_run_clusters(run_id: str, connection: ConnectionDep) -> dict:
    _run(connection, run_id)
    rows = connection.execute(text(
        "WITH access_by_cluster AS ("
        " SELECT ca.cluster_label, count(ar.accessibility_class)::integer AS assessed_count,"
        " count(*) FILTER (WHERE ar.accessibility_class = 'low')::integer AS low_count "
        " FROM cluster_assignments ca LEFT JOIN accessibility_results ar ON ar.cell_id = ca.cell_id "
        " WHERE ca.run_id = :id GROUP BY ca.cluster_label) "
        "SELECT cs.cluster_label, (cs.cluster_label = -1) AS is_noise, cs.cell_count, "
        "cs.area_m2, cs.sample_share, cs.feature_statistics, "
        "CASE WHEN ac.assessed_count = 0 THEN NULL "
        "ELSE ac.low_count::double precision / ac.assessed_count END AS low_access_share "
        "FROM cluster_statistics cs LEFT JOIN access_by_cluster ac USING (cluster_label) "
        "WHERE cs.run_id = :id ORDER BY cs.cluster_label"
    ), {"id": run_id}).mappings().all()
    return {"run_id": run_id, "items": [dict(row) for row in rows], "total": len(rows)}


@router.get("/ml/runs/{run_id}/assignments")
def ml_run_assignments(
    run_id: str,
    connection: ConnectionDep,
    limit: Annotated[int, Query(ge=1, le=CELL_LIMIT)] = 5000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    _run(connection, run_id)
    total = connection.execute(text(
        "SELECT count(*)::integer FROM cluster_assignments WHERE run_id = :id"
    ), {"id": run_id}).scalar_one()
    rows = connection.execute(text(
        "SELECT cell_id, cluster_label, (cluster_label = -1) AS is_noise "
        "FROM cluster_assignments WHERE run_id = :id "
        "ORDER BY cell_id LIMIT :limit OFFSET :offset"
    ), {"id": run_id, "limit": limit, "offset": offset}).mappings().all()
    return {
        "run_id": run_id, "items": [dict(row) for row in rows],
        "total": total, "limit": limit, "offset": offset,
    }
