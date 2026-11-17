"""Add selected Overture Places from a local GeoJSON file to the current services.

Download the `place` theme for Opole and its 2 km buffer with the Overture CLI.
Run this after import_osm and before features. The command does not retain source
metadata or create another dataset version.
"""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from shapely import Point
from sqlalchemy import Engine

from app.database import get_engine
from pipeline.geo import to_metric
from pipeline.service_import import add_points, reconcile_places


SHOP_TYPES = {"grocery_store", "supermarket", "convenience_store", "greengrocer",
              "bakery", "butcher", "butcher_shop", "pastry_shop"}
SCHOOL_TYPES = {"school", "primary_school", "elementary_school", "middle_school",
                "secondary_school", "high_school"}
EXCLUDED_SCHOOLS = {"language_school", "driving_school", "music_school", "dance_school",
                    "art_school"}
MIN_CONFIDENCE = 0.7  # conservative working threshold; Overture scores are not calibrated probabilities
NON_GENERAL_SCHOOL_NAME = re.compile(
    r"\b(?:j[ęe]zyk(?:ow|ów)\w*|language|nauki jazdy|driving|muzyczn\w*|music|tańc\w*|dance)\b", re.I
)
SCHOOL_NAME = re.compile(r"szko[łl]a|\bSP\s*(?:nr|\d)|\bPSP\s*\d|liceum|technikum|school", re.I)
NON_SCHOOL_NAME = re.compile(r"przedszkol|żłob|zlob|szkoleni|kurs\b", re.I)


def _object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _name(value) -> str | None:
    name = _object(value).get("primary")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _category(properties: dict) -> tuple[str, str] | None:
    taxonomy = _object(properties.get("taxonomy"))
    primary = taxonomy.get("primary")
    hierarchy = taxonomy.get("hierarchy")
    if not isinstance(primary, str):
        return None
    labels = {primary}
    if isinstance(hierarchy, list):
        labels.update(value for value in hierarchy if isinstance(value, str))
    if labels & SHOP_TYPES:
        return "shop", primary
    if labels & SCHOOL_TYPES and not labels & EXCLUDED_SCHOOLS:
        return "school", "primary" if labels & {"primary_school", "elementary_school"} else "general"
    return None


def parse_places(path: Path, city, buffer) -> tuple[list[dict], Counter]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("type") != "FeatureCollection"
            or not isinstance(payload.get("features"), list)):
        raise ValueError("Oczekiwano GeoJSON FeatureCollection z Overture Places.")
    records, counts = [], Counter()
    for feature in payload["features"]:
        if not isinstance(feature, dict):
            counts["invalid"] += 1
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            counts["invalid"] += 1
            continue
        if properties.get("theme", "places") != "places" or properties.get("type", "place") != "place":
            counts["other_theme"] += 1
            continue
        classified = _category(properties)
        if classified is None:
            counts["other_category"] += 1
            continue
        category, subcategory = classified
        if properties.get("operating_status") in {"permanently_closed", "temporarily_closed"}:
            counts["closed"] += 1
            continue
        confidence = properties.get("confidence")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not MIN_CONFIDENCE <= confidence <= 1):
            counts["low_confidence"] += 1
            continue
        name = _name(properties.get("names"))
        if name is None:
            counts["unnamed"] += 1
            continue
        if category == "school" and (
            NON_GENERAL_SCHOOL_NAME.search(name) or NON_SCHOOL_NAME.search(name)
            or not SCHOOL_NAME.search(name)
        ):
            counts["other_category"] += 1
            continue
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
            counts["invalid_geometry"] += 1
            continue
        lon, lat = coordinates[:2]
        if (not isinstance(lon, (int, float)) or not isinstance(lat, (int, float))
                or not math.isfinite(lon) or not math.isfinite(lat)
                or not (-180 <= lon <= 180 and -90 <= lat <= 90)):
            counts["invalid_geometry"] += 1
            continue
        point = to_metric(Point(lon, lat))
        if not buffer.covers(point):
            counts["outside_buffer"] += 1
            continue
        records.append({"name": name, "category": category, "subcategory": subcategory,
                        "geometry": point, "inside_city": bool(city.covers(point))})
    return records, counts


def run(path: Path, engine: Engine | None = None) -> dict:
    return add_points(engine or get_engine(), path, parse_places)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geojson", type=Path, help="Overture Places GeoJSON for Opole and a 2 km buffer")
    args = parser.parse_args()
    print(json.dumps(run(args.geojson), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
