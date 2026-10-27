"""Audit of OSM tags actually used in Opole (city + buffer) before deciding import rules.

Usage:
    python -m pipeline.osm_audit --list-boundaries
    python -m pipeline.osm_audit --relation-id <id> [--refresh]

Writes backend/data/reports/osm_audit_<date>.json and .md. Nothing is imported into the
database; the report only describes what OSM contains so the team can choose tags.
"""

import argparse
import json
import re
from collections import Counter
from itertools import combinations

from shapely import Point, STRtree

from app.core.config import BOUNDARY_BUFFER_M, OSM_ATTRIBUTION, get_settings
from pipeline.osm import element_point, overpass, relation_to_multipolygon, to_metric, to_wgs84

SEARCH_WINDOW = (50.55, 17.75, 50.80, 18.10)  # S, W, N, E around Opole, only to find candidate relations

SCHOOL_NAME_PATTERNS = {
    "szkoła podstawowa": re.compile(r"szko[łl]a\s+podstawowa|\bSP\s*(nr|\d)|publiczna\s+szko[łl]a\s+podst", re.I),
    "liceum": re.compile(r"liceum|\bL\.?O\.?\b", re.I),
    "technikum": re.compile(r"technikum", re.I),
    "szkoła branżowa": re.compile(r"bran[żz]ow", re.I),
    "zespół szkół": re.compile(r"zesp[óo][łl]\s+szk[óo][łl]|\bZS", re.I),
    "szkoła muzyczna/artystyczna": re.compile(r"muzyczn|artystyczn|plastyczn|baletow", re.I),
    "szkoła specjalna": re.compile(r"specjaln", re.I),
}
STOP_CODE = re.compile(r"\s*\(\d+\)\s*$")  # MZK stop-post code, e.g. "Wrocławska - Kościół (511)"
SCHOOL_DUPLICATE_DISTANCE_M = 150
STOP_PAIR_DISTANCE_M = 30


def boundary_candidates(refresh: bool) -> list[dict]:
    s, w, n, e = SEARCH_WINDOW
    query = (
        f'[out:json][timeout:120];relation["boundary"="administrative"]["name"="Opole"]'
        f"({s},{w},{n},{e});out tags bb;"
    )
    data = overpass(query, cache_name="audit_boundary_candidates", refresh=refresh)
    keys = ("admin_level", "teryt:terc", "teryt:simc", "wikidata", "place", "official_name", "border_type")
    return [
        {"id": el["id"], **{k: el["tags"].get(k) for k in keys}, "bounds": el.get("bounds")}
        for el in data["elements"]
    ]


def boundary_geometry(relation_id: int, refresh: bool):
    data = overpass(
        f"[out:json][timeout:180];relation({relation_id});out geom;",
        cache_name=f"audit_boundary_{relation_id}",
        refresh=refresh,
    )
    relation = next(el for el in data["elements"] if el["type"] == "relation")
    return relation, relation_to_multipolygon(relation), data


def services_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return (
        f"[out:json][timeout:300][bbox:{s},{w},{n},{e}];"
        '(nwr["shop"];nwr["amenity"="school"];nwr["highway"="bus_stop"];nwr["public_transport"];'
        'nwr["railway"~"^(station|halt|tram_stop)$"];);out tags center;'
    )


def stop_areas_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return f'[out:json][timeout:180][bbox:{s},{w},{n},{e}];relation["public_transport"="stop_area"];out body;'


def land_use_query(relation_id: int) -> str:
    area_id = 3_600_000_000 + relation_id
    parts = {
        "landuse=residential": 'nwr["landuse"="residential"](area.city);',
        "building=*": 'nwr["building"](area.city);',
        "natural=water / waterway=riverbank": '(nwr["natural"="water"](area.city);nwr["waterway"="riverbank"](area.city););',
        "landuse=forest / natural=wood": '(nwr["landuse"="forest"](area.city);nwr["natural"="wood"](area.city););',
    }
    body = "".join(f"{statement}out count;" for statement in parts.values())
    return f"[out:json][timeout:300];area(id:{area_id})->.city;{body}", list(parts)


def classify(element: dict, city, buffer) -> str | None:
    point = element_point(element)
    if point is None:
        return None
    geometry = Point(point)
    if city.covers(geometry):
        return "city"
    if buffer.covers(geometry):
        return "buffer"
    return None


def count_by_zone(elements: list[dict], key) -> dict:
    result = {"city": Counter(), "buffer": Counter()}
    for element in elements:
        for value in key(element):
            result[element["_zone"]][value] += 1
    return {zone: dict(counter.most_common()) for zone, counter in result.items()}


def metric_point(element: dict) -> Point:
    return to_metric(Point(element_point(element)))


def close_pairs(left: list[dict], right: list[dict], distance_m: float, same: bool) -> list[tuple]:
    points = [metric_point(el) for el in right]
    tree = STRtree(points)
    pairs = []
    for i, element in enumerate(left):
        for j in tree.query(metric_point(element), predicate="dwithin", distance=distance_m):
            if same and j <= i:
                continue
            if left[i] is right[j]:
                continue
            pairs.append((element, right[j], round(metric_point(element).distance(points[j]), 1)))
    return pairs


def describe(element: dict) -> dict:
    return {"osm": f"{element['type']}/{element['id']}", "name": element.get("tags", {}).get("name")}


def audit_shops(elements: list[dict]) -> dict:
    shops = [el for el in elements if "shop" in el["tags"]]
    return {
        "total_by_zone": dict(Counter(el["_zone"] for el in shops)),
        "by_element_type": dict(Counter(el["type"] for el in shops)),
        "shop_values_by_zone": count_by_zone(shops, lambda el: [el["tags"]["shop"]]),
        "without_name_by_value": dict(Counter(el["tags"]["shop"] for el in shops if "name" not in el["tags"]).most_common()),
    }


def audit_schools(elements: list[dict]) -> dict:
    schools = [el for el in elements if el["tags"].get("amenity") == "school"]

    def name_classes(el):
        name = el["tags"].get("name", "")
        found = [label for label, pattern in SCHOOL_NAME_PATTERNS.items() if pattern.search(name)]
        return found or (["inna nazwa"] if name else ["brak nazwy"])

    primary_by_name = {id(el) for el in schools if SCHOOL_NAME_PATTERNS["szkoła podstawowa"].search(el["tags"].get("name", ""))}
    isced = lambda el: str(el["tags"].get("isced:level", ""))
    duplicates = close_pairs(schools, schools, SCHOOL_DUPLICATE_DISTANCE_M, same=True)
    return {
        "total_by_zone": dict(Counter(el["_zone"] for el in schools)),
        "by_element_type": dict(Counter(el["type"] for el in schools)),
        "isced_level_by_zone": count_by_zone(schools, lambda el: [el["tags"].get("isced:level", "(brak)")]),
        "school_tag_by_zone": count_by_zone(schools, lambda el: [el["tags"].get("school", "(brak)")]),
        "grades_present": sum("grades" in el["tags"] for el in schools),
        "name_class_by_zone": count_by_zone(schools, name_classes),
        "primary_name_vs_isced": {
            "name_primary_and_isced_1": sum(id(el) in primary_by_name and "1" in isced(el).split(";") for el in schools),
            "name_primary_without_isced": sum(id(el) in primary_by_name and not isced(el) for el in schools),
            "isced_1_without_primary_name": sum(id(el) not in primary_by_name and "1" in isced(el).split(";") for el in schools),
        },
        "pairs_within_150m": [
            {"a": describe(a), "b": describe(b), "distance_m": d} for a, b, d in duplicates
        ],
        "operator_type_values": dict(Counter(el["tags"].get("operator:type", "(brak)") for el in schools).most_common()),
    }


def audit_stops(elements: list[dict], stop_areas: list[dict]) -> dict:
    def kind(el):
        tags = el["tags"]
        labels = []
        if tags.get("highway") == "bus_stop":
            labels.append("highway=bus_stop")
        if "public_transport" in tags:
            labels.append(f"public_transport={tags['public_transport']}")
        if tags.get("railway") in {"station", "halt", "tram_stop"}:
            labels.append(f"railway={tags['railway']}")
        return labels

    stops = [el for el in elements if kind(el)]
    combos = Counter(" + ".join(kind(el)) for el in stops)
    platforms = [el for el in stops if el["tags"].get("highway") == "bus_stop" or el["tags"].get("public_transport") == "platform"]
    positions = [el for el in stops if el["tags"].get("public_transport") == "stop_position"]
    member_refs = {(m["type"], m["ref"]) for rel in stop_areas for m in rel.get("members", [])}
    platform_in_area = sum((el["type"], el["id"]) in member_refs for el in platforms)
    same_name_pairs = [
        (a, b, d) for a, b, d in close_pairs(positions, platforms, STOP_PAIR_DISTANCE_M, same=False)
        if a["tags"].get("name") and a["tags"].get("name") == b["tags"].get("name")
    ]
    platform_names = Counter(el["tags"].get("name") for el in platforms if el["_zone"] == "city")
    rail = lambda el: el["tags"].get("railway") == "platform" or el["tags"].get("train") == "yes"
    city_bus = [el for el in platforms if el["_zone"] == "city" and not rail(el)]
    base_names = Counter(STOP_CODE.sub("", el["tags"]["name"]) for el in city_bus if el["tags"].get("name"))
    return {
        "tag_combinations_by_zone": count_by_zone(stops, lambda el: [" + ".join(kind(el))]),
        "tag_combinations_total": dict(combos.most_common()),
        "platform_like_by_zone": dict(Counter(el["_zone"] for el in platforms)),
        "platform_like_by_element_type": dict(Counter(el["type"] for el in platforms)),
        "platform_like_mode_tags": dict(Counter(
            ",".join(k for k in ("bus", "train", "tram", "trolleybus") if el["tags"].get(k) == "yes") or "(brak)"
            for el in platforms
        ).most_common()),
        "stop_position_by_zone": dict(Counter(el["_zone"] for el in positions)),
        "stop_area_relations": len(stop_areas),
        "platform_like_in_stop_area": platform_in_area,
        "platform_like_without_name": sum("name" not in el["tags"] for el in platforms),
        "city_platform_like_distinct_names": len([n for n in platform_names if n]),
        "city_platform_like_names_with_2plus_elements": sum(1 for n, c in platform_names.items() if n and c >= 2),
        "stop_position_near_same_name_platform_30m": len(same_name_pairs),
        "rail_platforms_by_zone": dict(Counter(el["_zone"] for el in platforms if rail(el))),
        "city_non_rail_platforms": len(city_bus),
        "city_non_rail_platforms_with_stop_code_in_name": sum(
            bool(STOP_CODE.search(el["tags"].get("name", ""))) for el in city_bus
        ),
        "city_non_rail_distinct_base_names": len(base_names),
        "city_non_rail_platforms_without_name": sum("name" not in el["tags"] for el in city_bus),
        "platform_like_operator": dict(Counter(el["tags"].get("operator", "(brak)") for el in platforms).most_common(8)),
    }


def to_markdown(report: dict) -> str:
    lines = [
        f"# Audyt tagów OSM – Opole (bufor {report['buffer_m']} m)",
        "",
        f"- Źródło: {report['source_url']}",
        f"- Stan danych OSM (osm_base): {report['osm_base']}",
        f"- Pobrano: {report['downloaded_at']}",
        f"- Licencja: {report['attribution']}",
        f"- Relacja granicy: {report['boundary']['relation_id']} ({report['boundary']['tags'].get('name')}, "
        f"admin_level={report['boundary']['tags'].get('admin_level')}), powierzchnia {report['boundary']['area_km2']} km²",
        "",
        "Pełne dane: plik JSON o tej samej nazwie.",
        "",
    ]
    for section in ("boundary_candidates", "shops", "schools", "stops", "land_use_counts_in_city"):
        lines += [f"## {section}", "", "```json", json.dumps(report[section], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def run(relation_id: int, refresh: bool) -> dict:
    candidates = boundary_candidates(refresh)
    relation, city, boundary_data = boundary_geometry(relation_id, refresh)
    city_metric = to_metric(city)
    for candidate in candidates:
        other = to_metric(boundary_geometry(candidate["id"], refresh)[1])
        candidate["area_km2"] = round(other.area / 1e6, 3)
        candidate["symmetric_difference_vs_selected_m2"] = round(other.symmetric_difference(city_metric).area, 1)
    buffer = to_wgs84(city_metric.buffer(BOUNDARY_BUFFER_M))
    w, s, e, n = buffer.bounds
    bbox = (round(s, 5), round(w, 5), round(n, 5), round(e, 5))

    services = overpass(services_query(bbox), cache_name="audit_services", refresh=refresh)
    stop_area_data = overpass(stop_areas_query(bbox), cache_name="audit_stop_areas", refresh=refresh)
    land_query, land_labels = land_use_query(relation_id)
    land_data = overpass(land_query, cache_name="audit_land_use_counts", refresh=refresh)

    elements = []
    for element in services["elements"]:
        element.setdefault("tags", {})
        zone = classify(element, city, buffer)
        if zone:
            element["_zone"] = zone
            elements.append(element)
    stop_areas = [el for el in stop_area_data["elements"] if el["type"] == "relation"]
    counts = [el for el in land_data["elements"] if el["type"] == "count"]

    return {
        "source_url": services["_download"]["source_url"],
        "osm_base": services["osm3s"]["timestamp_osm_base"],
        "downloaded_at": services["_download"]["downloaded_at"],
        "attribution": OSM_ATTRIBUTION,
        "buffer_m": BOUNDARY_BUFFER_M,
        "query_bbox": bbox,
        "boundary_candidates": candidates,
        "boundary": {
            "relation_id": relation["id"],
            "tags": relation.get("tags", {}),
            "area_km2": round(city_metric.area / 1e6, 2),
            "polygons": len(city.geoms),
            "osm_base": boundary_data["osm3s"]["timestamp_osm_base"],
        },
        "elements_in_analysis_area": len(elements),
        "elements_outside_buffer_dropped": len(services["elements"]) - len(elements),
        "shops": audit_shops(elements),
        "schools": audit_schools(elements),
        "stops": audit_stops(elements, stop_areas),
        "land_use_counts_in_city": {
            label: {k: int(v) for k, v in count["tags"].items()} for label, count in zip(land_labels, counts)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-boundaries", action="store_true")
    parser.add_argument("--relation-id", type=int)
    parser.add_argument("--refresh", action="store_true", help="ignore the local cache and download again")
    args = parser.parse_args()

    if args.list_boundaries or args.relation_id is None:
        for candidate in boundary_candidates(args.refresh):
            print(json.dumps(candidate, ensure_ascii=False))
        if args.relation_id is None:
            return

    report = run(args.relation_id, args.refresh)
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = reports_dir / f"osm_audit_{report['osm_base'][:10]}"
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    stem.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    print(f"Raport: {stem}.json / .md")


if __name__ == "__main__":
    main()
