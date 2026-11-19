"""All place names and coordinates in these tests are SYNTHETIC and fictitious."""

import json
import pytest

from shapely import Point, box

from pipeline.import_overture import parse_places, reconcile_places
from pipeline.geo import to_wgs84
from pipeline.service_import import same_school_name


@pytest.mark.parametrize("left,right,expected", [
    ("II Liceum Ogólnokształcące", "Publiczne Liceum Ogólnokształcące nr II z Oddziałami Dwujęzycznymi w Opolu", True),
    ("Liceum Ogólnokształcące nr VI im. Fikcyjnego Patrona", "Publiczne Liceum Ogolnoksztalcace nr 6", True),
    ("Publiczna Szkoła Podstawowa nr 5", "Publiczna Szkola Podstawowa nr 5 z Oddzialami Integracyjnymi", True),
    ("Niepubliczna Szkoła Podstawowa Nasza Szkoła", "Niepubliczna Szkoła Podstawowa Nasza Szkoła Opole", True),
    ("Liceum Ogólnokształcące nr II", "Szkoła Podstawowa nr 2", False),
    ("Szkoła Podstawowa nr 2", "Szkoła Podstawowa nr 21", False),
    ("Liceum Ogólnokształcące nr II", "Liceum Ogólnokształcące nr II dla Dorosłych", False),
    ("Publiczna Szkoła Podstawowa nr 2", "Niepubliczna Szkoła Podstawowa nr 2", False),
    (None, None, False),
])
def test_school_identity(left, right, expected):
    assert same_school_name(left, right) is expected


def test_same_school_name_does_not_merge_distant_campuses_or_different_types():
    existing = [{"name": "II Liceum Ogólnokształcące", "category": "school", "geometry": Point(0, 0)}]
    candidates = [
        {"name": "Liceum Ogólnokształcące nr 2", "category": "school", "geometry": Point(30, 0)},
        {"name": "Liceum Ogólnokształcące nr 2", "category": "school", "geometry": Point(500, 0)},
        {"name": "Szkoła Podstawowa nr 2", "category": "school", "geometry": Point(40, 0)},
    ]
    accepted, counts = reconcile_places(candidates, existing)
    assert accepted == candidates[1:]
    assert counts["duplicate"] == 1


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


def test_shop_locality_suffix_does_not_create_a_second_shop():
    existing = [{"name": "Fikcyjny Market", "category": "shop", "geometry": Point(0, 0)}]
    candidates = [
        {"name": "Fikcyjny Market Opole-Zakrzów", "category": "shop", "geometry": Point(20, 20)},
        {"name": "Fikcyjny Market Opole", "category": "shop", "geometry": Point(200, 0)},
        {"name": "Inny Market", "category": "shop", "geometry": Point(20, 0)},
    ]
    accepted, counts = reconcile_places(candidates, existing)
    assert [item["name"] for item in accepted] == ["Fikcyjny Market Opole", "Inny Market"]
    assert counts["duplicate"] == 1
