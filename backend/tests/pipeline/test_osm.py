"""SYNTHETIC fixtures only: geometries and tags below are fictitious."""

import json

import pytest
from shapely import box

from pipeline import osm
from pipeline.osm_audit import SCHOOL_NAME_PATTERNS, audit_shops, audit_stops, classify


def way(coords, role="outer"):
    return {"type": "way", "role": role, "geometry": [{"lon": x, "lat": y} for x, y in coords]}


def test_relation_with_split_outer_ring_and_hole():
    relation = {"id": 1, "members": [
        way([(0, 0), (2, 0), (2, 2)]),
        way([(2, 2), (0, 2), (0, 0)]),
        way([(0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1), (0.5, 0.5)], role="inner"),
        {"type": "node", "role": "admin_centre", "lon": 1, "lat": 1},
    ]}
    shape = osm.relation_to_multipolygon(relation)
    assert shape.geom_type == "MultiPolygon"
    assert shape.area == pytest.approx(4 - 0.25)


def test_relation_without_closed_ring_is_rejected():
    with pytest.raises(ValueError):
        osm.relation_to_multipolygon({"id": 2, "members": [way([(0, 0), (1, 0), (1, 1)])]})


def test_classify_city_buffer_and_outside():
    city, buffer = box(0, 0, 1, 1), box(-1, -1, 2, 2)
    assert classify({"type": "node", "lon": 0.5, "lat": 0.5}, city, buffer) == "city"
    assert classify({"type": "way", "center": {"lon": 1.5, "lat": 0.5}}, city, buffer) == "buffer"
    assert classify({"type": "node", "lon": 5, "lat": 5}, city, buffer) is None
    assert classify({"type": "relation"}, city, buffer) is None


def test_shop_audit_counts_every_value_separately():
    elements = [
        {"type": "node", "tags": {"shop": "supermarket"}, "_zone": "city"},
        {"type": "node", "tags": {"shop": "clothes", "name": "X"}, "_zone": "city"},
        {"type": "way", "tags": {"shop": "supermarket", "name": "Y"}, "_zone": "buffer"},
        {"type": "node", "tags": {"amenity": "school"}, "_zone": "city"},
    ]
    result = audit_shops(elements)
    assert result["shop_values_by_zone"]["city"] == {"supermarket": 1, "clothes": 1}
    assert result["shop_values_by_zone"]["buffer"] == {"supermarket": 1}
    assert result["without_name_by_value"] == {"supermarket": 1}


def test_stop_audit_detects_same_name_stop_position_next_to_platform():
    elements = [
        {"type": "node", "id": 1, "lon": 17.9, "lat": 50.67, "_zone": "city",
         "tags": {"highway": "bus_stop", "public_transport": "platform", "bus": "yes", "name": "A"}},
        {"type": "node", "id": 2, "lon": 17.9001, "lat": 50.67, "_zone": "city",
         "tags": {"public_transport": "stop_position", "bus": "yes", "name": "A"}},
        {"type": "node", "id": 3, "lon": 17.95, "lat": 50.67, "_zone": "city",
         "tags": {"highway": "bus_stop", "name": "B"}},
    ]
    stop_areas = [{"type": "relation", "id": 9, "members": [{"type": "node", "ref": 1, "role": "platform"}]}]
    result = audit_stops(elements, stop_areas)
    assert result["platform_like_by_zone"] == {"city": 2}
    assert result["platform_like_in_stop_area"] == 1
    assert result["stop_position_near_same_name_platform_30m"] == 1


@pytest.mark.parametrize("name, expected", [
    ("Publiczna Szkoła Podstawowa nr 5", True),
    ("SP nr 14", True),
    ("Liceum Ogólnokształcące nr II", False),
    ("Zespół Szkół Mechanicznych", False),
])
def test_primary_school_name_pattern(name, expected):
    assert bool(SCHOOL_NAME_PATTERNS["szkoła podstawowa"].search(name)) is expected


def test_overpass_uses_cache_without_network(tmp_path, monkeypatch):
    settings = osm.get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    calls = []

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"elements": [], "osm3s": {"timestamp_osm_base": "SYNTHETIC"}}

    monkeypatch.setattr(osm.requests, "post", lambda *a, **k: calls.append(1) or Response())
    monkeypatch.setattr(osm, "PAUSE_AFTER_REQUEST_S", 0)

    first = osm.overpass("[out:json];node(1);out;", cache_name="t")
    second = osm.overpass("[out:json];node(1);out;", cache_name="t")

    assert len(calls) == 1
    assert first == second
    cached = json.loads(next(tmp_path.glob("raw/t_*.json")).read_text(encoding="utf-8"))
    assert cached["_download"]["query"] == "[out:json];node(1);out;"
