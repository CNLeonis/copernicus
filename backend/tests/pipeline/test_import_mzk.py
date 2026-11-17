"""All stops and coordinates in this test are SYNTHETIC and fictitious."""

from zipfile import ZipFile

from shapely import Point, box

from pipeline.geo import to_wgs84
from pipeline.import_mzk import parse_stops
from pipeline.service_import import reconcile_places


def stop_xml(name, code, x, y, *, home="false"):
    lon, lat = to_wgs84(Point(x, y)).coords[0]
    return (f"<StopPoint><Descriptor><CommonName>{name}</CommonName></Descriptor>"
            f"<Place><Location><Longitude>{lon}</Longitude><Latitude>{lat}</Latitude>"
            f"</Location></Place><Extensions><PublicCode>{code}</PublicCode>"
            f"<IsForPassengers>true</IsForPassengers><IsHome>{home}</IsHome>"
            f"</Extensions></StopPoint>")


def test_mzk_stops_add_only_distinct_passenger_points(tmp_path):
    city = box(500_000, 300_000, 501_000, 301_000)
    xml = ("<StopPoints>" +
           stop_xml("Fikcyjna - Szkoła", "101", 500_100, 300_100) +
           stop_xml("Fikcyjna - Szkoła", "102", 500_105, 300_100) +
           stop_xml("Fikcyjna - Nowa", "103", 500_500, 300_500) +
           stop_xml("Fikcyjna Zajezdnia", "999", 500_600, 300_600, home="true") +
           "</StopPoints>")
    path = tmp_path / "synthetic_mzk.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("TransXChange.xml", xml)

    candidates, parsed = parse_stops(path, city, city.buffer(2000))
    existing = [{"name": "Fikcyjna - Szkoła (101)", "category": "bus_stop",
                 "geometry": Point(500_110, 300_100)}]
    accepted, matched = reconcile_places(candidates, existing)

    assert parsed["not_passenger_stop"] == 1
    assert matched["duplicate"] == 1
    assert [item["name"] for item in accepted] == ["Fikcyjna - Szkoła (102)", "Fikcyjna - Nowa (103)"]
