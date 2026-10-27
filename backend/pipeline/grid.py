"""Build regular analysis grids for Opole in EPSG:2180.

Usage:
    python -m pipeline.grid [--size 250] [--size 500] [--data-version VERSION] [--replace]

Squares are aligned to multiples of ORIGIN_ALIGNMENT_M, so 250 m cells nest exactly
inside 500 m cells. Each cell keeps the full square and its part inside the city.
Rules and working parameters are described in docs/data_sources.md.
"""

import argparse
import json
import math
import re
from collections import Counter

import numpy as np
import shapely
from shapely import MultiPolygon, Point, Polygon, STRtree, box
from sqlalchemy import Engine, text

from app.core.config import GRID_SIZES_M, METRIC_CRS, METRIC_SRID, get_settings
from app.database import get_engine
from pipeline.import_osm import identity_checksum

ORIGIN_ALIGNMENT_M = 1000
MIN_AREA_SHARE = 0.1  # working parameter: city fragments below 10% of a full cell are slivers
MIN_BUILDINGS = 1  # working parameter for the "potentially residential" evidence
MIN_LAND_PART_M2 = 1.0  # smaller land parts left after removing water/forest are numerical slivers


def grid_version(size: int, data_version: str) -> str:
    return f"g{size}-{re.sub(r'[^0-9A-Za-z]', '', data_version)}"


def cell_id(version: str, col: int, row: int) -> str:
    return f"{version}-c{col:03d}-r{row:03d}"


def grid_origin(bounds, alignment: int = ORIGIN_ALIGNMENT_M) -> tuple[float, float]:
    return math.floor(bounds[0] / alignment) * alignment, math.floor(bounds[1] / alignment) * alignment


def polygons_of(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 0 else []
    if hasattr(geometry, "geoms"):
        return [polygon for part in geometry.geoms for polygon in polygons_of(part)]
    return []


def clipped_union(tree: STRtree | None, geometries: np.ndarray, clip):
    if tree is None:
        return Polygon()
    index = tree.query(clip, predicate="intersects")
    if not len(index):
        return Polygon()
    return shapely.union_all(shapely.intersection(geometries[index], clip))


def choose_point(clip, blocked) -> tuple[Point, str]:
    """Centroid if it lies in the cell and outside water/forest, otherwise a point on the largest land part."""
    centroid = clip.centroid
    if clip.contains(centroid) and not blocked.covers(centroid):
        return centroid, "centroid"
    land = [p for p in polygons_of(clip.difference(blocked)) if p.area >= MIN_LAND_PART_M2]
    target = max(land, key=lambda p: p.area) if land else max(polygons_of(clip), key=lambda p: p.area)
    point = target.representative_point()
    if not clip.contains(point):
        # overlay noise can leave the point on the cell edge; shrink the part by 1 cm and retry
        inner = target.buffer(-0.01)
        point = inner.representative_point() if not inner.is_empty else clip.representative_point()
    return point, "representative_point"


def point_land_cover(point: Point, water, forest) -> str:
    if not water.is_empty and water.covers(point):
        return "water"
    if not forest.is_empty and forest.covers(point):
        return "forest"
    return "ok"


def building_counts(buildings: np.ndarray, city, size: int, origin: tuple[float, float]) -> Counter:
    """Each building is counted once, in the cell containing its point on surface."""
    if not len(buildings):
        return Counter()
    points = shapely.point_on_surface(buildings)
    points = points[shapely.covers(city, points)]
    cols = np.floor((shapely.get_x(points) - origin[0]) / size).astype(int)
    rows = np.floor((shapely.get_y(points) - origin[1]) / size).astype(int)
    return Counter(zip(cols.tolist(), rows.tolist()))


def build_cells(city, size: int, layers: dict[str, list], version: str) -> list[dict]:
    """Cut the city into size x size cells and describe each cell (metric CRS)."""
    origin = grid_origin(city.bounds)
    cols = math.ceil((city.bounds[2] - origin[0]) / size)
    rows = math.ceil((city.bounds[3] - origin[1]) / size)
    shapely.prepare(city)

    arrays = {kind: np.array(layers.get(kind, []), dtype=object) for kind in ("residential", "water", "forest")}
    trees = {kind: STRtree(geoms) if len(geoms) else None for kind, geoms in arrays.items()}
    buildings = building_counts(np.array(layers.get("building", []), dtype=object), city, size, origin)

    cells = []
    for col in range(cols):
        for row in range(rows):
            x, y = origin[0] + col * size, origin[1] + row * size
            square = box(x, y, x + size, y + size)
            if not city.intersects(square):
                continue
            parts = [square] if city.contains_properly(square) else polygons_of(square.intersection(city))
            if not parts:
                continue
            clip = MultiPolygon(parts)
            water = clipped_union(trees["water"], arrays["water"], clip)
            forest = clipped_union(trees["forest"], arrays["forest"], clip)
            residential_area = clipped_union(trees["residential"], arrays["residential"], clip).area
            point, method = choose_point(clip, shapely.union(water, forest))
            building_count = buildings.get((col, row), 0)
            cells.append({
                "id": cell_id(version, col, row),
                "col": col,
                "row": row,
                "geometry": clip,
                "full_geometry": square,
                "representative_point": point,
                "representative_point_method": method,
                "point_land_cover": point_land_cover(point, water, forest),
                "area_m2": clip.area,
                "building_count": building_count,
                "residential_area_m2": residential_area,
                "land_use_status": "potentially_residential"
                if residential_area > 0 or building_count >= MIN_BUILDINGS else "no_residential_evidence",
                "analysis_status": "excluded_sliver" if clip.area < MIN_AREA_SHARE * size * size else "included",
            })
    return cells


def summarize(cells: list[dict], city) -> dict:
    included = [c for c in cells if c["analysis_status"] == "included"]
    evidence = Counter(
        "residential_landuse" if c["residential_area_m2"] > 0
        else "buildings_only" if c["building_count"] >= MIN_BUILDINGS else "none"
        for c in included
    )
    return {
        "cells": len(cells),
        "analysis_status": dict(Counter(c["analysis_status"] for c in cells)),
        "cells_area_km2": round(sum(c["area_m2"] for c in cells) / 1e6, 3),
        "city_area_km2": round(city.area / 1e6, 3),
        "representative_point_method": dict(Counter(c["representative_point_method"] for c in cells)),
        "point_land_cover": dict(Counter(c["point_land_cover"] for c in cells)),
        "included_land_use_status": dict(Counter(c["land_use_status"] for c in included)),
        "included_residential_evidence": dict(evidence),
        "checksum_sha256": identity_checksum(
            f"{c['id']}|{c['area_m2']:.1f}|{c['analysis_status']}|{c['land_use_status']}" for c in cells),
    }


# --- database --------------------------------------------------------------------------

def load_import(engine: Engine, data_version: str | None) -> dict:
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT d.id, d.data_version, b.id, ST_AsBinary(b.geometry) FROM data_imports d "
            "JOIN city_boundary b ON b.import_id = d.id WHERE d.import_status = 'completed' "
            "AND (CAST(:version AS text) IS NULL OR d.data_version = :version) ORDER BY d.id DESC LIMIT 1"
        ), {"version": data_version}).first()
        if row is None:
            raise SystemExit("Brak zaimportowanych danych OSM; najpierw uruchom python -m pipeline.import_osm.")
        layers: dict[str, list] = {}
        for kind, wkb in connection.execute(text(
                "SELECT kind, ST_AsBinary(geometry) FROM land_cover WHERE import_id = :id ORDER BY id"),
                {"id": row[0]}):
            layers.setdefault(kind, []).append(shapely.from_wkb(bytes(wkb)))
    return {"import_id": row[0], "data_version": row[1], "boundary_id": row[2],
            "city": shapely.from_wkb(bytes(row[3])), "layers": layers}


def write_grid(engine: Engine, version: str, boundary_id: int, size: int, parameters: dict,
               cells: list[dict], replace: bool) -> bool:
    origin = (parameters["origin_x"], parameters["origin_y"])
    with engine.begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM grid_versions WHERE grid_version = :v"), {"v": version}).scalar()
        if exists and not replace:
            return False
        if exists:
            connection.execute(text("DELETE FROM grid_versions WHERE grid_version = :v"), {"v": version})
        connection.execute(text(
            "INSERT INTO grid_versions (grid_version, boundary_id, cell_size_m, origin_x, origin_y, crs, parameters) "
            "VALUES (:version, :boundary_id, :size, :x, :y, :crs, CAST(:parameters AS jsonb))"
        ), {"version": version, "boundary_id": boundary_id, "size": size, "x": origin[0], "y": origin[1],
            "crs": METRIC_CRS, "parameters": json.dumps(parameters, ensure_ascii=False)})
        connection.execute(text(
            "INSERT INTO analysis_cells (id, grid_version, col, row, geometry, full_geometry, representative_point, "
            "representative_point_method, point_land_cover, area_m2, building_count, residential_area_m2, "
            "land_use_status, analysis_status) VALUES (:id, :version, :col, :row, "
            "ST_Multi(ST_GeomFromWKB(:geometry, :srid)), ST_GeomFromWKB(:full_geometry, :srid), "
            "ST_GeomFromWKB(:point, :srid), :representative_point_method, :point_land_cover, :area_m2, "
            ":building_count, :residential_area_m2, :land_use_status, :analysis_status)"
        ), [{**{k: c[k] for k in ("id", "col", "row", "representative_point_method", "point_land_cover", "area_m2",
                                  "building_count", "residential_area_m2", "land_use_status", "analysis_status")},
             "version": version, "srid": METRIC_SRID, "geometry": c["geometry"].wkb,
             "full_geometry": c["full_geometry"].wkb, "point": c["representative_point"].wkb} for c in cells])
    return True


def run(sizes, data_version: str | None = None, replace: bool = False) -> list[dict]:
    engine = get_engine()
    source = load_import(engine, data_version)
    city = source["city"]
    reports = []
    for size in sizes:
        version = grid_version(size, source["data_version"])
        cells = build_cells(city, size, source["layers"], version)
        origin = grid_origin(city.bounds)
        parameters = {
            "data_version": source["data_version"],
            "cell_size_m": size,
            "origin_x": origin[0],
            "origin_y": origin[1],
            "origin_alignment_m": ORIGIN_ALIGNMENT_M,
            "min_area_share": MIN_AREA_SHARE,
            "min_buildings": MIN_BUILDINGS,
            "min_land_part_m2": MIN_LAND_PART_M2,
            "summary": summarize(cells, city),
        }
        written = write_grid(engine, version, source["boundary_id"], size, parameters, cells, replace)
        reports.append({"grid_version": version, "written": written, **parameters})
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", type=int, action="append", help="cell size in metres (default: 250 and 500)")
    parser.add_argument("--data-version", help="data_imports.data_version (default: latest completed import)")
    parser.add_argument("--replace", action="store_true", help="delete and re-create the grid (cascades)")
    args = parser.parse_args()

    reports = run(args.size or GRID_SIZES_M, args.data_version, args.replace)
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        status = "Zapisano" if report["written"] else "Siatka już istnieje w bazie; pominięto (użyj --replace)"
        print(f"{status}: {report['grid_version']}")
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        (reports_dir / f"grid_{report['grid_version']}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
