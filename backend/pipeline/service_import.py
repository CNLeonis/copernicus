"""Shared matching and database write for additional service points."""

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Callable

import shapely
from sqlalchemy import Engine, text

from app.core.config import BOUNDARY_BUFFER_M, METRIC_SRID

MATCH_DISTANCE_M = {"shop": 50, "school": 150, "bus_stop": 30}
AMBIGUOUS_DISTANCE_M = 10
STOP_CODE = re.compile(r"\s*\((\d+)\)\s*$")


def _normalized(name: str | None) -> str:
    return " ".join(name.casefold().split()) if name else ""


def _shop_name(name: str | None) -> str:
    """Compare a shop name with its optional Opole/local district suffix."""
    name = re.sub(r"[\W_]+", " ", (name or "").casefold()).strip()
    return re.sub(r"\s+opole(?:\s+\w+)?$", "", name)


def school_identity(name: str | None) -> tuple | None:
    """Match school names, retaining type, number and distinguishing qualifiers."""
    name = (name or "").casefold().replace("ł", "l")
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    if not name:
        return None
    qualifiers = tuple(word for word in ("niepubliczna", "niepubliczne", "doroslych", "specjalna", "specjalne") if word in name.split())
    name = re.sub(r"^(?:publiczna|publiczne|niepubliczna|niepubliczne)\s+", "", name)
    name = re.sub(r"\s+(?:w opolu|opole)$", "", name)
    name = name.replace("z oddzialami dwujezycznymi", "").strip()
    match = re.match(r"^(szkola podstawowa|psp|sp|liceum ogolnoksztalcace|plo|lo)\s+(?:nr\s+)?(\d+|[ivx]+)\b", name)
    if not match:
        leading = re.match(r"^([ivx]+)\s+(liceum ogolnoksztalcace)\b", name)
        if leading:
            kind, number = leading[2], leading[1]
        else:
            return ("name", " ".join(name.split()), qualifiers)
    else:
        kind, number = match[1], match[2]
    if not number.isdigit():
        values = {"i": 1, "v": 5, "x": 10}
        number = sum(-values[c] if i + 1 < len(number) and values[c] < values[number[i + 1]]
                     else values[c] for i, c in enumerate(number))
    return ("primary" if kind in {"szkola podstawowa", "psp", "sp"} else "secondary", int(number), qualifiers)


def same_school_name(left: str | None, right: str | None) -> bool:
    identity = school_identity(left)
    return identity is not None and identity == school_identity(right)


def _stop_code(name: str | None) -> str | None:
    match = STOP_CODE.search(name or "")
    return match.group(1) if match else None


def _same_stop(candidate: dict, existing: dict, distance: float) -> bool:
    candidate_code = _stop_code(candidate["name"])
    existing_code = _stop_code(existing["name"])
    if candidate_code and existing_code:
        return candidate_code == existing_code and distance <= 150
    return distance <= 30 and _normalized(STOP_CODE.sub("", candidate["name"])) == _normalized(
        STOP_CODE.sub("", existing["name"] or "")
    )


def reconcile_places(candidates: list[dict], existing: list[dict]) -> tuple[list[dict], Counter]:
    """Avoid likely duplicates; leave uncertain nearby objects out of the count."""
    accepted, counts = [], Counter()
    known = list(existing)
    for candidate in candidates:
        category = candidate["category"]
        nearby = [(item, item["geometry"].distance(candidate["geometry"]))
                  for item in known if item["category"] == category and
                  item["geometry"].distance(candidate["geometry"]) <=
                  (150 if category == "bus_stop" else MATCH_DISTANCE_M[category])]
        if category == "bus_stop":
            duplicate = any(_same_stop(candidate, item, distance) for item, distance in nearby)
        elif category == "shop":
            name = _shop_name(candidate["name"])
            duplicate = bool(name) and any(_shop_name(item["name"]) == name for item, _ in nearby)
        elif category == "school":
            duplicate = any(same_school_name(item["name"], candidate["name"]) for item, _ in nearby)
        else:
            duplicate = any(_normalized(item["name"]) == _normalized(candidate["name"])
                            for item, _ in nearby)
        if duplicate:
            counts["duplicate"] += 1
            continue
        if any(distance <= AMBIGUOUS_DISTANCE_M and not
               (category == "bus_stop" and _stop_code(candidate["name"]) and
                _stop_code(item["name"]) and _stop_code(candidate["name"]) != _stop_code(item["name"]))
               for item, distance in nearby):
            counts["ambiguous"] += 1
            continue
        accepted.append(candidate)
        known.append(candidate)
    counts["added"] = len(accepted)
    return accepted, counts


def add_points(engine: Engine, path: Path, parse: Callable) -> dict:
    with engine.begin() as connection:
        boundary_wkb = connection.execute(text(
            "SELECT ST_AsBinary(geometry) FROM city_boundary LIMIT 1"
        )).scalar()
        if boundary_wkb is None:
            raise ValueError("Najpierw zaimportuj granicę Opola poleceniem pipeline.import_osm.")
        city = shapely.from_wkb(bytes(boundary_wkb))
        candidates, counts = parse(path, city, city.buffer(BOUNDARY_BUFFER_M))
        existing = [{"name": name, "category": category, "geometry": shapely.from_wkb(bytes(wkb))}
                    for name, category, wkb in connection.execute(text(
                        "SELECT name, category, ST_AsBinary(geometry) FROM services ORDER BY id"
                    ))]
        accepted, matched = reconcile_places(candidates, existing)
        counts.update(matched)
        if accepted:
            connection.execute(text(
                "INSERT INTO services (name, category, subcategory, geometry, inside_city) "
                "VALUES (:name, :category, :subcategory, ST_GeomFromWKB(:geometry, :srid), :inside_city)"
            ), [{**{key: item[key] for key in ("name", "category", "subcategory", "inside_city")},
                 "geometry": item["geometry"].wkb, "srid": METRIC_SRID} for item in accepted])
            connection.execute(text("DELETE FROM ml_runs"))
            connection.execute(text("DELETE FROM accessibility_results"))
            connection.execute(text("DELETE FROM cell_features"))
    return dict(counts)
