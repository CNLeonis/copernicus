"""Add passenger bus stops from the official MZK Opole TransXChange ZIP."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from shapely import Point
from sqlalchemy import Engine

from app.database import get_engine
from pipeline.geo import to_metric
from pipeline.service_import import add_points


def parse_stops(path: Path, city, buffer) -> tuple[list[dict], Counter]:
    with ZipFile(path) as archive:
        xml_files = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if len(xml_files) != 1:
            raise ValueError("Pakiet MZK musi zawierać dokładnie jeden plik XML.")
        data = archive.read(xml_files[0])
    start = data.find(b"<StopPoints>")
    end = data.find(b"</StopPoints>", start)
    if start < 0 or end < 0:
        raise ValueError("W pakiecie MZK nie znaleziono sekcji StopPoints.")
    root = ET.fromstring(data[start:end + len(b"</StopPoints>")])
    records, counts = [], Counter()
    for stop in root.findall("StopPoint"):
        if stop.findtext("Extensions/IsForPassengers") != "true" or stop.findtext("Extensions/IsHome") == "true":
            counts["not_passenger_stop"] += 1
            continue
        name = stop.findtext("Descriptor/CommonName")
        code = stop.findtext("Extensions/PublicCode")
        try:
            lon = float(stop.findtext("Place/Location/Longitude"))
            lat = float(stop.findtext("Place/Location/Latitude"))
        except (TypeError, ValueError):
            counts["invalid"] += 1
            continue
        if (not name or not name.strip() or not math.isfinite(lon) or not math.isfinite(lat)
                or not (-180 <= lon <= 180 and -90 <= lat <= 90)):
            counts["invalid"] += 1
            continue
        point = to_metric(Point(lon, lat))
        if not buffer.covers(point):
            counts["outside_buffer"] += 1
            continue
        label = name.strip() + (f" ({code.strip()})" if code and code.strip() else "")
        records.append({"name": label, "category": "bus_stop", "subcategory": "bus_tram_stop",
                        "geometry": point, "inside_city": bool(city.covers(point))})
    return records, counts


def run(path: Path, engine: Engine | None = None) -> dict:
    return add_points(engine or get_engine(), path, parse_stops)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_file", type=Path, help="Official MZK Opole TransXChange ZIP")
    args = parser.parse_args()
    print(json.dumps(run(args.zip_file), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
