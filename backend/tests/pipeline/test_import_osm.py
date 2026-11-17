"""SYNTHETIC fixtures only: all objects, names and coordinates below are fictitious."""

import pytest
from shapely import MultiPolygon, Point, box
from sqlalchemy import text

from pipeline.import_osm import (
    build_services, classify_school, classify_shop, classify_stop, isced_levels,
    merge_school_representations, merge_stop_representations, write_import,
)
from pipeline.geo import to_wgs84


@pytest.mark.parametrize("value, expected", [
    ("supermarket", "supermarket"), ("bakery", "bakery"), ("pastry", "pastry"),
    ("variety_store", None), ("wholesale", None), ("kiosk", None), ("yes", None), ("clothes", None),
])
def test_grocery_shop_rule(value, expected):
    assert classify_shop({"shop": value}) == expected


@pytest.mark.parametrize("tags, expected", [
    ({"amenity": "school", "isced:level": "0;1;2"}, ("primary", None)),
    ({"amenity": "school", "isced:level": "1-3"}, ("primary", None)),
    ({"amenity": "school", "name": "Publiczna Szkoła Podstawowa nr 99"}, ("primary", None)),
    ({"amenity": "school", "name": "SP nr 7"}, ("primary", None)),
    ({"amenity": "school", "name": "Liceum Ogólnokształcące nr X", "isced:level": "3"}, ("general", None)),
    ({"amenity": "school", "name": "Społeczne Językowe Liceum"}, ("general", None)),
    ({"amenity": "school"}, ("general", None)),
    ({"amenity": "school", "name": "Fikcyjne Language Club"}, (None, "language_school")),
    ({"amenity": "school", "name": "Centrum Języków Obcych Fikcja"}, (None, "language_school")),
])
def test_school_rule(tags, expected):
    assert classify_school(tags) == expected


def test_isced_levels_parse_lists_and_ranges():
    assert isced_levels("0;1;2") == {"0", "1", "2"}
    assert isced_levels("2-3") == {"2", "3"}
    assert isced_levels("") == set()


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "bus_stop", "public_transport": "platform"}, "bus_tram_stop"),
    ({"highway": "bus_stop"}, "bus_tram_stop"),
    ({"public_transport": "platform", "railway": "platform"}, None),
    ({"public_transport": "platform", "train": "yes"}, None),
    ({"public_transport": "stop_position", "bus": "yes"}, None),
])
def test_stop_rule(tags, expected):
    assert classify_stop(tags) == expected


def school(source_type, source_id, name, point, polygon=None):
    return {"source_type": source_type, "source_id": source_id, "name": name,
            "geometry": Point(point), "polygon": polygon}


def test_school_merge_rules():
    outline = box(0, 0, 100, 100)
    schools = [
        school("node", 1, "SP Fikcyjna", (50, 50)),
        school("way", 10, "sp  fikcyjna", (40, 40), outline),          # same name, contains node 1 -> merged
        school("node", 2, "Liceum Fikcyjne", (250, 50)),
        school("way", 20, "Inna Szkoła", (240, 40), box(200, 0, 300, 100)),  # different name -> kept
        school("node", 3, "Zespół Fikcyjny", (450, 50)),
        school("way", 30, None, (440, 40), box(400, 0, 500, 100)),      # unnamed, one node -> merged
        school("node", 4, "A", (650, 50)), school("node", 5, "B", (660, 50)),
        school("way", 40, None, (640, 40), box(600, 0, 700, 100)),      # unnamed, two nodes -> kept
        school("node", 6, "Blisko", (820, 50)),
        school("way", 50, "Blisko", (740, 40), box(700, 0, 800, 100)),  # same name but node outside -> kept
    ]
    kept, merges = merge_school_representations(schools)

    kept_ids = {(s["source_type"], s["source_id"]) for s in kept}
    assert ("way", 10) not in kept_ids and ("way", 30) not in kept_ids
    assert {("way", 20), ("way", 40), ("way", 50), ("node", 6)} <= kept_ids
    assert {m["rule"] for m in merges} == {"same_name_point_in_outline", "single_named_point_in_unnamed_outline"}
    assert len(merges) == 2


CITY = box(0, 0, 1000, 1000)
BUFFER = CITY.buffer(2000)


def node(osm_id, x, y, **tags):
    lon, lat = to_wgs84(Point(x, y)).coords[0]
    return {"type": "node", "id": osm_id, "lon": lon, "lat": lat, "tags": tags}


def test_merge_only_matching_coded_node_and_way_stops():
    def stop(source_type, source_id, name, x):
        return {"category": "bus_stop", "source_type": source_type,
                "source_id": source_id, "name": name, "geometry": Point(x, 0)}

    records = [
        stop("node", 1, "Fikcyjna (119)", 0),
        stop("way", 2, "Fikcyjna (119)", 4),
        stop("way", 3, "Fikcyjna (120)", 5),
        stop("way", 4, "Fikcyjna (119)", 20),
        stop("node", 5, "Fikcyjna", 30),
        stop("way", 6, "Fikcyjna", 31),
    ]
    kept, merges = merge_stop_representations(records)

    assert [r["source_id"] for r in kept] == [1, 3, 4, 5, 6]
    assert merges == [{"kept": "node/1", "dropped": "way/2", "name": "Fikcyjna (119)"}]


def test_build_services_categories_buffer_and_single_count():
    # Fictitious metric coordinates around an arbitrary EPSG:2180 origin.
    ox, oy = 500_000, 300_000
    city = box(ox, oy, ox + 1000, oy + 1000)
    buffer = city.buffer(2000)
    service_data = {"elements": [
        node(1, ox + 10, oy + 10, shop="supermarket"),
        node(2, ox + 1500, oy + 10, shop="bakery"),                       # buffer zone
        node(3, ox + 9000, oy + 10, shop="convenience"),                  # outside buffer -> dropped
        node(4, ox + 20, oy + 20, shop="clothes"),                        # not grocery
        node(5, ox + 30, oy + 30, highway="bus_stop", public_transport="platform"),  # counted once
        node(6, ox + 40, oy + 40, public_transport="platform", railway="platform"),
    ]}
    school_data = {"elements": [
        node(7, ox + 50, oy + 50, amenity="school", name="Fikcyjna Szkoła Podstawowa"),
        node(8, ox + 60, oy + 60, amenity="school", name="Fikcyjne Language Club"),
    ]}
    records, report = build_services(service_data, school_data, city, buffer)

    assert report["counts"] == {
        "shop": {"supermarket": {"city": 1}, "bakery": {"buffer": 1}},
        "bus_stop": {"bus_tram_stop": {"city": 1}},
        "school": {"primary": {"city": 1}},
    }
    assert report["dropped_outside_buffer"] == {"shop": 1}
    assert [s["name"] for s in report["excluded"]["language_schools"]] == ["Fikcyjne Language Club"]
    assert all(r["geometry"].geom_type == "Point" for r in records)


def test_empty_import_cannot_replace_existing_data():
    city = MultiPolygon([box(500_000, 300_000, 501_000, 301_000)])
    with pytest.raises(ValueError, match="żadnych usług"):
        write_import(None, {"tags": {"name": "SYNTHETIC"}}, city, [], [], replace=True)


@pytest.mark.db
def test_write_import_is_idempotent_and_stores_metric_geometries(db):
    city = MultiPolygon([box(500_000, 300_000, 501_000, 301_000)])
    services = [{"source_type": "node", "source_id": 1, "name": None, "category": "shop",
                 "subcategory": "supermarket", "geometry": Point(500_100, 300_100),
                 "inside_city": True}]
    land = [{"source_type": "way", "source_id": 2, "kind": "building",
             "geometry": box(500_200, 300_200, 500_210, 300_210)}]
    relation = {"id": 99, "tags": {"name": "SYNTHETIC"}}

    assert write_import(db, relation, city, services, land, replace=False)
    assert not write_import(db, relation, city, services, land, replace=False)
    with db.connect() as c:
        row = c.execute(text(
            "SELECT (SELECT count(*) FROM services), (SELECT ST_SRID(geometry) FROM services LIMIT 1), "
            "(SELECT count(*) FROM land_cover), ST_Area(geometry) FROM city_boundary"
        )).one()
    assert row == (1, 2180, 1, pytest.approx(1_000_000))
    assert write_import(db, relation, city, services, land, replace=True)
