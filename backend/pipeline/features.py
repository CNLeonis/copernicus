"""Compute versioned Euclidean accessibility features for analysis cells.

Usage:
    python -m pipeline.features [--grid-version VERSION] [--replace]

All distances are in EPSG:2180 metres. The pipeline processes all cells, including
boundary slivers; later analyses can filter by ``analysis_status`` and land use.
"""

import argparse
import hashlib
import json
import math

import shapely
from shapely import Point, STRtree
from sqlalchemy import Engine, text

from app.core.config import BOUNDARY_BUFFER_M, CATEGORIES, FEATURE_COLUMNS, METRIC_SRID, RADII_M, get_settings
from app.database import get_engine

FEATURE_METHOD_VERSION = 1

STATUS_COLUMNS = {"shop": "shop_status", "school": "school_status", "bus_stop": "bus_stop_status"}
DISTANCE_COLUMNS = {
    "shop": "nearest_shop_m",
    "school": "nearest_school_m",
    "bus_stop": "nearest_bus_stop_m",
}
COUNT_COLUMNS = {
    ("shop", 500): "shops_500m",
    ("shop", 1000): "shops_1000m",
    ("school", 500): "schools_500m",
    ("school", 1000): "schools_1000m",
    ("bus_stop", 500): "bus_stops_500m",
    ("bus_stop", 1000): "bus_stops_1000m",
}


def feature_version(grid_version: str, import_id: int, parameters: dict) -> str:
    """Return a stable ID tied to the grid, OSM import and computation settings."""
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"f-{grid_version}-{import_id}-{digest}"


def _tree_for(geometries: list[Point]) -> STRtree | None:
    return STRtree(geometries) if geometries else None


def _nearest_distance(tree: STRtree | None, point: Point) -> float | None:
    if tree is None:
        return None
    indices, distances = tree.query_nearest(point, return_distance=True)
    if len(indices) == 0:
        return None
    distance = float(distances[0])
    return distance if math.isfinite(distance) else None


def _count_within(tree: STRtree | None, point: Point, radius_m: int) -> int:
    if tree is None:
        return 0
    indices = tree.query(point, predicate="dwithin", distance=radius_m)
    return len(indices)


def calculate_cell_features(
    cells: list[dict],
    services: dict[str, list[Point]],
    *,
    source_buffer_m: float,
    radii_m: tuple[int, ...] = RADII_M,
) -> list[dict]:
    """Compute all nine features and data status fields for each cell.

    A null distance means the nearest service cannot be established from the
    imported snapshot, not zero metres. For a point inside the city, the source
    coverage radius is its distance to the city boundary plus the OSM buffer;
    a returned candidate is a proven nearest only when it falls within that
    radius. Counts are zero only when the source category exists and the imported
    buffer covers the requested radius. ``buffer_sufficient`` is true only when
    all category nearest distances are established and the buffer covers the
    largest configured radius.
    """
    if not radii_m or any(radius <= 0 for radius in radii_m):
        raise ValueError("radii_m must contain positive distances")
    trees = {category: _tree_for(services.get(category, [])) for category in CATEGORIES}
    largest_radius = max(radii_m)
    radius_coverage = {radius: source_buffer_m >= radius for radius in radii_m}
    rows = []

    for cell in cells:
        point = cell["representative_point"]
        row = {"cell_id": cell["id"]}
        all_nearest_found = True

        for category in CATEGORIES:
            tree = trees[category]
            service_exists = tree is not None
            nearest = _nearest_distance(tree, point)
            row[DISTANCE_COLUMNS[category]] = None
            if not service_exists:
                status = "no_source"
                all_nearest_found = False
            elif nearest is None or nearest > cell.get("coverage_radius_m", source_buffer_m):
                # Imported objects outside the proven coverage could omit a closer
                # service just beyond the city buffer, so do not present them as nearest.
                row[DISTANCE_COLUMNS[category]] = None
                status = "none_found"
                all_nearest_found = False
            else:
                row[DISTANCE_COLUMNS[category]] = nearest
                status = "ok"

            row[STATUS_COLUMNS[category]] = status
            for radius in radii_m:
                key = COUNT_COLUMNS.get((category, radius))
                if key is None:
                    raise ValueError(f"No database feature column for {category} radius {radius} m")
                row[key] = (
                    _count_within(tree, point, radius)
                    if service_exists and radius_coverage[radius]
                    else None
                )

        row["buffer_sufficient"] = (
            source_buffer_m >= largest_radius and all_nearest_found
        )
        rows.append(row)
    return rows


def _load_input(engine: Engine, requested_grid_version: str | None) -> dict:
    with engine.connect() as connection:
        grid = connection.execute(text(
            "SELECT gv.grid_version, gv.boundary_id, cb.import_id, di.data_version, "
            "COALESCE(NULLIF(di.validation_report->>'buffer_m', '')::double precision, 0) AS buffer_m "
            "FROM grid_versions gv "
            "JOIN city_boundary cb ON cb.id = gv.boundary_id "
            "JOIN data_imports di ON di.id = cb.import_id "
            "WHERE di.import_status = 'completed' "
            "AND (CAST(:grid_version AS text) IS NULL OR gv.grid_version = :grid_version) "
            "ORDER BY gv.created_at DESC, gv.grid_version DESC LIMIT 1"
        ), {"grid_version": requested_grid_version}).first()
        if grid is None:
            raise SystemExit("Brak siatki dla ukończonego importu; najpierw uruchom pipeline.grid.")

        cells = [{
            "id": cell_id,
            "representative_point": shapely.from_wkb(bytes(point_wkb)),
            "coverage_radius_m": float(boundary_distance) + float(grid[4]),
        } for cell_id, point_wkb, boundary_distance in connection.execute(text(
            "SELECT a.id, ST_AsBinary(a.representative_point), "
            "ST_Distance(ST_Boundary(cb.geometry), a.representative_point) "
            "FROM analysis_cells a JOIN city_boundary cb ON cb.id = :boundary_id "
            "WHERE a.grid_version = :grid ORDER BY a.id"),
            {"grid": grid[0], "boundary_id": grid[1]})]

        services: dict[str, list[Point]] = {category: [] for category in CATEGORIES}
        for category, wkb in connection.execute(text(
            "SELECT category, ST_AsBinary(geometry) FROM services "
            "WHERE import_id = :import_id ORDER BY category, id"), {"import_id": grid[2]}):
            services[category].append(shapely.from_wkb(bytes(wkb)))

    return {
        "grid_version": grid[0],
        "boundary_id": grid[1],
        "import_id": grid[2],
        "data_version": grid[3],
        "source_buffer_m": float(grid[4]),
        "cells": cells,
        "services": services,
    }


def _write_features(engine: Engine, version: str, grid: dict, parameters: dict,
                    rows: list[dict], replace: bool) -> bool:
    with engine.begin() as connection:
        exists = connection.execute(text(
            "SELECT 1 FROM feature_versions WHERE feature_version = :version"),
            {"version": version}).scalar()
        if exists and not replace:
            return False
        if exists:
            # Cascades to feature rows and any downstream runs/assessments tied to this version.
            connection.execute(text(
                "DELETE FROM feature_versions WHERE feature_version = :version"),
                {"version": version})

        connection.execute(text(
            "INSERT INTO feature_versions (feature_version, grid_version, import_id, distance_method, parameters) "
            "VALUES (:version, :grid_version, :import_id, 'euclidean', CAST(:parameters AS jsonb))"
        ), {
            "version": version,
            "grid_version": grid["grid_version"],
            "import_id": grid["import_id"],
            "parameters": json.dumps(parameters, ensure_ascii=False, sort_keys=True),
        })

        columns = ["cell_id", "feature_version", *FEATURE_COLUMNS,
                   "shop_status", "school_status", "bus_stop_status", "buffer_sufficient"]
        value_names = ["cell_id", "feature_version", *FEATURE_COLUMNS,
                       "shop_status", "school_status", "bus_stop_status", "buffer_sufficient"]
        sql = text(
            "INSERT INTO cell_features (" + ", ".join(columns) + ") VALUES (" +
            ", ".join(f":{name}" for name in value_names) + ")"
        )
        connection.execute(sql, [
            {**row, "feature_version": version} for row in rows
        ])
    return True


def run(grid_version: str | None = None, replace: bool = False) -> dict:
    engine = get_engine()
    source = _load_input(engine, grid_version)
    parameters = {
        "method_version": FEATURE_METHOD_VERSION,
        "distance_method": "euclidean",
        "crs": f"EPSG:{METRIC_SRID}",
        "anchor": "analysis_cells.representative_point",
        "radii_m": list(RADII_M),
        "source_buffer_m": source["source_buffer_m"],
        "expected_buffer_m": BOUNDARY_BUFFER_M,
        "category_status": {
            "ok": "source exists and at least one object found in imported snapshot",
            "none_found": "source category exists but no object found within provably covered extent",
            "no_source": "import contains no objects for this category",
        },
        "buffer_sufficient_definition": (
            "source buffer covers the largest configured radius and a nearest object "
            "was found for every category within source_buffer_m + representative-point "
            "distance to the city boundary"
        ),
        "source_count_by_category": {
            category: len(source["services"][category]) for category in CATEGORIES
        },
    }
    version = feature_version(source["grid_version"], source["import_id"], parameters)
    rows = calculate_cell_features(
        source["cells"], source["services"], source_buffer_m=source["source_buffer_m"]
    )
    written = _write_features(engine, version, source, parameters, rows, replace)
    status_counts = {
        column: {status: sum(row[column] == status for row in rows)
                 for status in ("ok", "none_found", "no_source", "error")}
        for column in STATUS_COLUMNS.values()
    }
    return {
        "feature_version": version,
        "grid_version": source["grid_version"],
        "data_version": source["data_version"],
        "cell_count": len(rows),
        "written": written,
        "buffer_sufficient_cells": sum(row["buffer_sufficient"] for row in rows),
        "status_counts": status_counts,
        "parameters": parameters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid-version", help="grid_versions.grid_version (default: latest)")
    parser.add_argument("--replace", action="store_true",
                        help="recreate this feature version; cascades to associated ML/assessment results")
    args = parser.parse_args()
    report = run(args.grid_version, args.replace)
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    name = f"features_{report['feature_version']}.json"
    (reports_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
