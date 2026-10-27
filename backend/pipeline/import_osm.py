"""Import OSM data for Opole into PostGIS, pinned to a fixed OSM state.

Usage:
    python -m pipeline.import_osm [--refresh] [--replace]

All Overpass queries use the attic header [date:"OSM_DATE"], so every team member
downloads exactly the same objects. Import rules follow the team decisions of
2026-10-27 described in docs/data_sources.md.
"""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict

import shapely
from shapely import MultiPolygon, Point, Polygon, STRtree
from sqlalchemy import Engine, text

from app.core.config import BOUNDARY_BUFFER_M, METRIC_CRS, METRIC_SRID, OSM_ATTRIBUTION, get_settings
from app.database import get_engine
from pipeline.osm import element_point, overpass, relation_to_multipolygon, to_metric, to_wgs84
from pipeline.osm_audit import SCHOOL_NAME_PATTERNS

OSM_DATE = "2026-10-27T13:49:01Z"  # osm_base of the audit run; attic queries return this exact state
DATA_VERSION = f"osm-{OSM_DATE}"
RELATION_ID = 2730708  # gmina miejska Opole, TERYT 1661011
SOURCE_NAME = "OpenStreetMap (Overpass API)"

GROCERY_SHOPS = ("supermarket", "convenience", "grocery", "greengrocer", "bakery", "butcher", "pastry")
PRIMARY_SCHOOL_NAME = SCHOOL_NAME_PATTERNS["szkoła podstawowa"]
LANGUAGE_SCHOOL_NAME = re.compile(r"\blanguage\b|j[ęe]zyk[óo]w\s+obcych|szko[łl]a\s+j[ęe]zykow", re.I)
SCHOOL_PAIR_REPORT_DISTANCE_M = 150

LAND_COVER_FILTERS = {
    "residential": ['wr["landuse"="residential"]'],
    "building": ['wr["building"]'],
    "water": ['wr["natural"="water"]', 'wr["waterway"="riverbank"]'],
    "forest": ['wr["landuse"="forest"]', 'wr["natural"="wood"]'],
}


def header(timeout: int = 600) -> str:
    return f'[out:json][timeout:{timeout}][date:"{OSM_DATE}"]'


def bbox_filter(geometry_wgs84) -> str:
    w, s, e, n = geometry_wgs84.bounds
    return f"[bbox:{s:.5f},{w:.5f},{n:.5f},{e:.5f}]"


# --- classification rules (team decisions a-c) -------------------------------------------

def classify_shop(tags: dict) -> str | None:
    value = tags.get("shop")
    return value if value in GROCERY_SHOPS else None


def isced_levels(value: str) -> set[str]:
    levels = set()
    for part in value.replace(",", ";").split(";"):
        part = part.strip()
        if re.fullmatch(r"\d+-\d+", part):
            low, high = map(int, part.split("-"))
            levels |= {str(level) for level in range(low, high + 1)}
        elif part:
            levels.add(part)
    return levels


def classify_school(tags: dict) -> tuple[str | None, str | None]:
    """Return (subcategory, exclusion_reason)."""
    if tags.get("amenity") != "school":
        return None, "not_school"
    name = tags.get("name", "")
    if LANGUAGE_SCHOOL_NAME.search(name):
        return None, "language_school"
    if "1" in isced_levels(tags.get("isced:level", "")) or PRIMARY_SCHOOL_NAME.search(name):
        return "primary", None
    return "general", None


def classify_stop(tags: dict) -> str | None:
    if tags.get("highway") != "bus_stop" and tags.get("public_transport") != "platform":
        return None
    if tags.get("railway") == "platform" or tags.get("train") == "yes":
        return "rail_platform"
    return "bus_tram_stop"


def normalize_name(name: str | None) -> str | None:
    return " ".join(name.casefold().split()) if name else None


def merge_school_representations(schools: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge a school node with the school outline that contains it.

    Explicit rule: a node is merged into a way/relation outline only if the node lies
    inside the outline AND the normalized names are equal, or the outline has no name
    and contains exactly this one school node. Proximity alone never merges objects.
    """
    nodes = [s for s in schools if s["source_type"] == "node"]
    outlines = [s for s in schools if s["polygon"] is not None]
    inside = defaultdict(list)
    for node in nodes:
        for outline in outlines:
            if outline["polygon"].covers(node["geometry"]):
                inside[id(outline)].append(node)

    dropped, merges = set(), []
    for outline in outlines:
        contained = inside[id(outline)]
        outline_name = normalize_name(outline["name"])
        for node in contained:
            same_name = outline_name is not None and normalize_name(node["name"]) == outline_name
            sole_in_unnamed = outline_name is None and len(contained) == 1 and node["name"]
            if not (same_name or sole_in_unnamed):
                continue
            dropped.add(id(outline))
            merges.append({
                "kept": f"node/{node['source_id']}",
                "dropped": f"{outline['source_type']}/{outline['source_id']}",
                "name": node["name"],
                "rule": "same_name_point_in_outline" if same_name else "single_named_point_in_unnamed_outline",
            })
            break
    return [s for s in schools if id(s) not in dropped], merges


def close_school_pairs(schools: list[dict], distance_m: float) -> list[dict]:
    points = [s["geometry"] for s in schools]
    tree = STRtree(points)
    pairs = []
    for i, school in enumerate(schools):
        for j in tree.query(school["geometry"], predicate="dwithin", distance=distance_m):
            if j > i:
                other = schools[j]
                pairs.append({
                    "a": f"{school['source_type']}/{school['source_id']}", "a_name": school["name"],
                    "b": f"{other['source_type']}/{other['source_id']}", "b_name": other["name"],
                    "distance_m": round(school["geometry"].distance(other["geometry"]), 1),
                })
    return pairs


# --- geometry -----------------------------------------------------------------------------

def element_polygon(element: dict):
    """Metric polygon of a way/relation fetched with `out geom`, or None."""
    if element["type"] == "way":
        coords = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        if len(coords) < 4 or coords[0] != coords[-1]:
            return None
        return to_metric(Polygon(coords))
    if element["type"] == "relation":
        try:
            return to_metric(relation_to_multipolygon(element))
        except ValueError:
            return None
    return None


def service_record(element: dict, category: str, subcategory: str, geometry: Point, city) -> dict:
    tags = element.get("tags", {})
    return {
        "source_type": element["type"],
        "source_id": element["id"],
        "name": tags.get("name"),
        "category": category,
        "subcategory": subcategory,
        "geometry": geometry,
        "source_tags": tags,
        "inside_city": bool(city.covers(geometry)),
    }


# --- download -----------------------------------------------------------------------------

def fetch_boundary(refresh: bool):
    data = overpass(f"{header(180)};relation({RELATION_ID});out geom;", cache_name="import_boundary", refresh=refresh)
    relation = next(el for el in data["elements"] if el["type"] == "relation")
    return relation, to_metric(relation_to_multipolygon(relation)), data


def fetch_services(buffer_wgs84, refresh: bool):
    shop_regex = "|".join(GROCERY_SHOPS)
    query = (
        f"{header()}{bbox_filter(buffer_wgs84)};"
        f'(nwr["shop"~"^({shop_regex})$"];nwr["highway"="bus_stop"];nwr["public_transport"="platform"];);'
        "out tags center;"
    )
    return overpass(query, cache_name="import_services", refresh=refresh)


def fetch_schools(buffer_wgs84, refresh: bool):
    query = f'{header()}{bbox_filter(buffer_wgs84)};nwr["amenity"="school"];out geom;'
    return overpass(query, cache_name="import_schools", refresh=refresh)


def fetch_land_cover(kind: str, city_wgs84, refresh: bool):
    statements = "".join(f"{statement};" for statement in LAND_COVER_FILTERS[kind])
    query = f"{header(900)}{bbox_filter(city_wgs84)};({statements});out geom;"
    return overpass(query, cache_name=f"import_land_cover_{kind}", refresh=refresh)


# --- build records ------------------------------------------------------------------------

def build_services(service_data: dict, school_data: dict, city, buffer) -> tuple[list[dict], dict]:
    report = {"raw_elements": {}, "dropped_outside_buffer": Counter(), "without_geometry": Counter(),
              "excluded": {"language_schools": []}}
    records = []

    report["raw_elements"]["shops_and_stops"] = len(service_data["elements"])
    for element in service_data["elements"]:
        tags = element.get("tags", {})
        for category, subcategory in (("shop", classify_shop(tags)), ("bus_stop", classify_stop(tags))):
            if subcategory is None:
                continue
            point = element_point(element)
            if point is None:
                report["without_geometry"][category] += 1
                continue
            geometry = to_metric(Point(point))
            if not buffer.covers(geometry):
                report["dropped_outside_buffer"][category] += 1
                continue
            records.append(service_record(element, category, subcategory, geometry, city))

    report["raw_elements"]["schools"] = len(school_data["elements"])
    schools = []
    for element in school_data["elements"]:
        tags = element.get("tags", {})
        subcategory, reason = classify_school(tags)
        if reason == "language_school":
            report["excluded"]["language_schools"].append(
                {"osm": f"{element['type']}/{element['id']}", "name": tags.get("name")})
            continue
        if subcategory is None:
            continue
        polygon = element_polygon(element)
        if element["type"] == "node":
            geometry = to_metric(Point(element["lon"], element["lat"]))
        elif polygon is not None:
            geometry = polygon.representative_point()
        else:
            report["without_geometry"]["school"] += 1
            continue
        if not buffer.covers(geometry):
            report["dropped_outside_buffer"]["school"] += 1
            continue
        record = service_record(element, "school", subcategory, geometry, city)
        record["polygon"] = polygon
        schools.append(record)

    kept, merges = merge_school_representations(schools)
    report["school_merges"] = merges
    report["school_pairs_within_150m_after_merge"] = close_school_pairs(kept, SCHOOL_PAIR_REPORT_DISTANCE_M)
    for school in kept:
        school.pop("polygon")
    records.extend(kept)

    counts = defaultdict(lambda: defaultdict(Counter))
    for record in records:
        counts[record["category"]][record["subcategory"]]["city" if record["inside_city"] else "buffer"] += 1
    report["counts"] = {cat: {sub: dict(zones) for sub, zones in subs.items()} for cat, subs in counts.items()}
    report["missing_name"] = dict(Counter(r["category"] for r in records if not r["name"]))
    report["dropped_outside_buffer"] = dict(report["dropped_outside_buffer"])
    report["without_geometry"] = dict(report["without_geometry"])
    report["checksum_sha256"] = identity_checksum(
        f"{r['category']}|{r['subcategory']}|{r['source_type']}/{r['source_id']}|{int(r['inside_city'])}" for r in records)
    return records, report


def build_land_cover(kind: str, data: dict, city) -> tuple[list[dict], dict]:
    stats = Counter(raw=len(data["elements"]))
    records = []
    for element in data["elements"]:
        polygon = element_polygon(element)
        if polygon is None:
            stats["not_polygon"] += 1
            continue
        if not polygon.is_valid:
            polygon = shapely.make_valid(polygon)
            stats["repaired"] += 1
        if not polygon.intersects(city):
            stats["outside_city"] += 1
            continue
        records.append({"source_type": element["type"], "source_id": element["id"], "kind": kind, "geometry": polygon})
    stats["stored"] = len(records)
    return records, {**dict(stats), "checksum_sha256": identity_checksum(
        f"{r['source_type']}/{r['source_id']}" for r in records)}


def identity_checksum(lines) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


# --- database -----------------------------------------------------------------------------

def write_import(engine: Engine, report: dict, relation: dict, city, services: list[dict],
                 land_cover: list[dict], replace: bool) -> int | None:
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT id FROM data_imports WHERE data_version = :v"), {"v": DATA_VERSION}).scalar()
        if existing and not replace:
            return None
        if existing:
            connection.execute(text("DELETE FROM data_imports WHERE id = :id"), {"id": existing})

        import_id = connection.execute(text(
            "INSERT INTO data_imports (source_name, source_url, downloaded_at, data_version, license_info, "
            "record_count, import_status, validation_report) VALUES (:name, :url, :downloaded_at, :version, "
            ":license, :count, 'completed', CAST(:report AS jsonb)) RETURNING id"
        ), {
            "name": SOURCE_NAME, "url": get_settings().overpass_url, "downloaded_at": report["downloaded_at"],
            "version": DATA_VERSION, "license": OSM_ATTRIBUTION, "count": 1 + len(services) + len(land_cover),
            "report": json.dumps(report, ensure_ascii=False),
        }).scalar_one()

        connection.execute(text(
            "INSERT INTO city_boundary (import_id, name, source_type, source_id, geometry) "
            "VALUES (:import_id, :name, 'relation', :source_id, ST_Multi(ST_GeomFromWKB(:wkb, :srid)))"
        ), {"import_id": import_id, "name": relation["tags"].get("name"), "source_id": relation["id"],
            "wkb": city.wkb, "srid": METRIC_SRID})

        connection.execute(text(
            "INSERT INTO services (import_id, source_type, source_id, name, category, subcategory, geometry, "
            "source_tags, inside_city) VALUES (:import_id, :source_type, :source_id, :name, :category, "
            ":subcategory, ST_GeomFromWKB(:wkb, :srid), CAST(:tags AS jsonb), :inside_city)"
        ), [{**{k: s[k] for k in ("source_type", "source_id", "name", "category", "subcategory", "inside_city")},
             "import_id": import_id, "wkb": s["geometry"].wkb, "srid": METRIC_SRID,
             "tags": json.dumps(s["source_tags"], ensure_ascii=False)} for s in services])

        if land_cover:
            connection.execute(text(
                "INSERT INTO land_cover (import_id, source_type, source_id, kind, geometry) "
                "VALUES (:import_id, :source_type, :source_id, :kind, ST_GeomFromWKB(:wkb, :srid))"
            ), [{"import_id": import_id, "source_type": r["source_type"], "source_id": r["source_id"],
                 "kind": r["kind"], "wkb": r["geometry"].wkb, "srid": METRIC_SRID} for r in land_cover])
    return import_id


def run(refresh: bool = False, replace: bool = False) -> dict:
    relation, city, boundary_data = fetch_boundary(refresh)
    city_wgs84 = to_wgs84(city)
    buffer = city.buffer(BOUNDARY_BUFFER_M)
    buffer_wgs84 = to_wgs84(buffer)

    service_data = fetch_services(buffer_wgs84, refresh)
    school_data = fetch_schools(buffer_wgs84, refresh)
    services, service_report = build_services(service_data, school_data, city, buffer)

    land_cover, land_report = [], {}
    downloads = [boundary_data, service_data, school_data]
    for kind in LAND_COVER_FILTERS:
        data = fetch_land_cover(kind, city_wgs84, refresh)
        downloads.append(data)
        records, land_report[kind] = build_land_cover(kind, data, city)
        land_cover.extend(records)

    report = {
        "data_version": DATA_VERSION,
        "osm_date": OSM_DATE,
        "downloaded_at": min(d["_download"]["downloaded_at"] for d in downloads),
        "crs": METRIC_CRS,
        "buffer_m": BOUNDARY_BUFFER_M,
        "boundary": {"relation_id": relation["id"], "area_km2": round(city.area / 1e6, 3), "valid": city.is_valid},
        "services": service_report,
        "land_cover": land_report,
        "queries": [d["_download"]["query"] for d in downloads],
    }
    import_id = write_import(get_engine(), report, relation, city, services, land_cover, replace)
    report["import_id"] = import_id
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="ignore the local cache and download again")
    parser.add_argument("--replace", action="store_true", help="delete and re-create this data_version (cascades)")
    args = parser.parse_args()

    report = run(args.refresh, args.replace)
    if report["import_id"] is None:
        print(f"Import {DATA_VERSION} już istnieje w bazie; pominięto (użyj --replace, aby odtworzyć).")
    else:
        print(f"Zaimportowano {DATA_VERSION} (data_imports.id={report['import_id']}).")
    summary = {
        "services": report["services"]["counts"],
        "services_checksum_sha256": report["services"]["checksum_sha256"],
        "school_merges": len(report["services"]["school_merges"]),
        "language_schools_excluded": len(report["services"]["excluded"]["language_schools"]),
        "land_cover": {k: {"stored": v["stored"], "checksum_sha256": v["checksum_sha256"]}
                       for k, v in report["land_cover"].items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "import_osm_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
