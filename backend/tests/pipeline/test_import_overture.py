"""All place names and coordinates in these tests are SYNTHETIC and fictitious."""

import json

from shapely import Point, box

from pipeline.import_overture import parse_places, reconcile_places
from pipeline.geo import to_wgs84


def place(x, y, name, category, *, confidence=0.9, status="open"):
    lon, lat = to_wgs84(Point(x, y)).coords[0]
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"theme": "places", "type": "place", "names": {"primary": name},
                           "taxonomy": {"primary": category, "hierarchy": [category]},
                           "confidence": confidence, "operating_status": status}}


def test_parse_places_selects_relevant_open_points_in_buffer(tmp_path):
    city = box(500_000, 300_000, 501_000, 301_000)
    features = [
        place(500_100, 300_100, "Fikcyjny Sklep", "grocery_store"),
        place(502_000, 300_100, "Fikcyjna Szkoła", "school"),
        place(500_200, 300_100, "Fikcyjna Restauracja", "restaurant"),
        place(500_300, 300_100, "Fikcyjny Sklep Zamknięty", "grocery_store", status="permanently_closed"),
        place(500_400, 300_100, "Fikcyjny Sklep Niepewny", "grocery_store", confidence=0.3),
        place(500_500, 300_100, "Fikcyjna Szkoła Językowa", "school"),
        place(505_000, 300_100, "Fikcyjny Sklep Daleko", "grocery_store"),
    ]
    path = tmp_path / "synthetic_places.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")

    records, counts = parse_places(path, city, city.buffer(2000))

    assert [(record["name"], record["category"], record["inside_city"]) for record in records] == [
        ("Fikcyjny Sklep", "shop", True), ("Fikcyjna Szkoła", "school", False)]
    assert counts["closed"] == counts["low_confidence"] == counts["outside_buffer"] == 1


def test_parse_places_accepts_json_encoded_overture_properties(tmp_path):
    city = box(500_000, 300_000, 501_000, 301_000)
    feature = place(500_100, 300_100, "Fikcyjny Sklep", "grocery_store")
    feature["properties"]["names"] = json.dumps(feature["properties"]["names"])
    feature["properties"]["taxonomy"] = json.dumps(feature["properties"]["taxonomy"])
    path = tmp_path / "synthetic_places.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8")

    records, _ = parse_places(path, city, city.buffer(2000))

    assert len(records) == 1
    assert records[0]["category"] == "shop"


def test_reconcile_does_not_double_count_same_or_uncertain_place():
    existing = [{"name": "Fikcyjny Market", "category": "shop", "geometry": Point(500_000, 300_000)},
                {"name": None, "category": "school", "geometry": Point(500_300, 300_000)}]
    candidates = [
        {"name": "fikcyjny  market", "category": "shop", "geometry": Point(500_020, 300_000)},
        {"name": "Inny Market", "category": "shop", "geometry": Point(500_005, 300_000)},
        {"name": "Inny Market", "category": "shop", "geometry": Point(500_100, 300_000)},
        {"name": "Fikcyjna Szkoła", "category": "school", "geometry": Point(500_302, 300_000)},
    ]

    accepted, counts = reconcile_places(candidates, existing)

    assert [item["name"] for item in accepted] == ["Inny Market"]
    assert counts == {"duplicate": 1, "ambiguous": 2, "added": 1}
