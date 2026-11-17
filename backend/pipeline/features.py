"""Compute Euclidean accessibility features for analysis cells.

Usage:
    python -m pipeline.features [--cell-size-m 250] [--replace]

All distances are in EPSG:2180 metres. The pipeline processes all cells, including
boundary slivers; later analyses can filter by ``analysis_status`` and land use.
"""

import argparse
import math

import shapely
from shapely import Point, STRtree
from sqlalchemy import Engine, text

from app.core.config import BOUNDARY_BUFFER_M, CATEGORIES, FEATURE_COLUMNS, RADII_M
from app.database import get_engine

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
    coverage radius is its distance to the city boundary plus the import buffer;
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


def _load_input(engine: Engine, cell_size_m: int) -> dict:
    with engine.connect() as connection:
        boundary = connection.execute(text("SELECT id FROM city_boundary LIMIT 1")).scalar()
        if boundary is None:
            raise SystemExit("Brak granicy miasta; najpierw uruchom pipeline.import_osm.")
        cells = [{
            "id": cell_id,
            "representative_point": shapely.from_wkb(bytes(point_wkb)),
            "coverage_radius_m": float(boundary_distance) + BOUNDARY_BUFFER_M,
        } for cell_id, point_wkb, boundary_distance in connection.execute(text(
            "SELECT a.id, ST_AsBinary(a.representative_point), "
            "ST_Distance(ST_Boundary(cb.geometry), a.representative_point) "
            "FROM analysis_cells a CROSS JOIN city_boundary cb "
            "WHERE a.cell_size_m = :size ORDER BY a.id"), {"size": cell_size_m})]
        if not cells:
            raise SystemExit(f"Brak siatki {cell_size_m} m; najpierw uruchom pipeline.grid.")

        services: dict[str, list[Point]] = {category: [] for category in CATEGORIES}
        for category, wkb in connection.execute(text(
            "SELECT category, ST_AsBinary(geometry) FROM services ORDER BY category, id")):
            services[category].append(shapely.from_wkb(bytes(wkb)))

    return {"cells": cells, "services": services}


def _write_features(engine: Engine, cell_size_m: int, rows: list[dict], replace: bool) -> bool:
    with engine.begin() as connection:
        exists = connection.execute(text(
            "SELECT 1 FROM cell_features f JOIN analysis_cells c ON c.id = f.cell_id "
            "WHERE c.cell_size_m = :size LIMIT 1"
        ), {"size": cell_size_m}).scalar()
        if exists and not replace:
            return False
        if exists:
            connection.execute(text("DELETE FROM ml_runs WHERE cell_size_m = :size"), {"size": cell_size_m})
            connection.execute(text(
                "DELETE FROM accessibility_results WHERE cell_id IN "
                "(SELECT id FROM analysis_cells WHERE cell_size_m = :size)"
            ), {"size": cell_size_m})
            connection.execute(text(
                "DELETE FROM cell_features WHERE cell_id IN "
                "(SELECT id FROM analysis_cells WHERE cell_size_m = :size)"
            ), {"size": cell_size_m})

        columns = ["cell_id", *FEATURE_COLUMNS,
                   "shop_status", "school_status", "bus_stop_status", "buffer_sufficient"]
        sql = text(
            "INSERT INTO cell_features (" + ", ".join(columns) + ") VALUES (" +
            ", ".join(f":{name}" for name in columns) + ")"
        )
        connection.execute(sql, rows)
    return True


def run(cell_size_m: int = 250, replace: bool = False) -> dict:
    engine = get_engine()
    source = _load_input(engine, cell_size_m)
    rows = calculate_cell_features(
        source["cells"], source["services"], source_buffer_m=BOUNDARY_BUFFER_M
    )
    written = _write_features(engine, cell_size_m, rows, replace)
    status_counts = {
        column: {status: sum(row[column] == status for row in rows)
                 for status in ("ok", "none_found", "no_source", "error")}
        for column in STATUS_COLUMNS.values()
    }
    return {
        "cell_size_m": cell_size_m,
        "cell_count": len(rows),
        "written": written,
        "buffer_sufficient_cells": sum(row["buffer_sufficient"] for row in rows),
        "status_counts": status_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cell-size-m", type=int, default=250, help="grid cell size in metres (default: 250)")
    parser.add_argument("--replace", action="store_true",
                        help="recalculate features and remove dependent ML/assessment results")
    args = parser.parse_args()
    report = run(args.cell_size_m, args.replace)
    print(f"Cechy {report['cell_size_m']} m: {report['cell_count']} komórek, zapisano={report['written']}")


if __name__ == "__main__":
    main()
