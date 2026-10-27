"""Overpass client with a local file cache and OSM geometry helpers.

Responses are cached in backend/data/raw so repeated runs do not hit Overpass again.
Requests are sequential with a pause and an identifying User-Agent, following the
Overpass usage policy (https://wiki.openstreetmap.org/wiki/Overpass_API).
"""

import hashlib
import json
import time
from datetime import datetime, timezone

import requests
import shapely
from pyproj import Transformer
from shapely import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

from app.core.config import METRIC_CRS, WGS84, get_settings

USER_AGENT = "copernicus-opole-academic-project/0.1 (+https://github.com/CNLeonis/copernicus)"
PAUSE_AFTER_REQUEST_S = 5
RETRY_STATUSES = {429, 502, 503, 504}
RETRY_WAITS_S = (30, 60, 120)

_TO_METRIC = Transformer.from_crs(WGS84, METRIC_CRS, always_xy=True).transform
_TO_WGS84 = Transformer.from_crs(METRIC_CRS, WGS84, always_xy=True).transform


def to_metric(geometry):
    return shapely.transform(geometry, lambda x, y: _TO_METRIC(x, y), interleaved=False)


def to_wgs84(geometry):
    return shapely.transform(geometry, lambda x, y: _TO_WGS84(x, y), interleaved=False)


def overpass(query: str, *, cache_name: str, refresh: bool = False) -> dict:
    settings = get_settings()
    raw_dir = settings.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    path = raw_dir / f"{cache_name}_{digest}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    for wait in (*RETRY_WAITS_S, None):
        response = requests.post(
            settings.overpass_url,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=600,
        )
        if response.status_code not in RETRY_STATUSES or wait is None:
            break
        print(f"Overpass HTTP {response.status_code}, ponowienie za {wait} s")
        time.sleep(wait)
    response.raise_for_status()

    payload = response.json()
    if payload.get("remark"):
        raise RuntimeError(f"Overpass zwrócił uwagę (niepełny wynik?): {payload['remark']}")
    payload["_download"] = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": settings.overpass_url,
        "query": query,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSE_AFTER_REQUEST_S)
    return payload


def relation_to_multipolygon(relation: dict) -> MultiPolygon:
    """Build a (lon, lat) multipolygon from a relation returned with `out geom`."""
    lines = {"outer": [], "inner": []}
    for member in relation.get("members", []):
        if member.get("type") != "way" or "geometry" not in member:
            continue
        role = "inner" if member.get("role") == "inner" else "outer"
        lines[role].append(LineString([(p["lon"], p["lat"]) for p in member["geometry"]]))
    outer = unary_union(list(polygonize(unary_union(lines["outer"]))))
    inner = unary_union(list(polygonize(unary_union(lines["inner"])))) if lines["inner"] else None
    shape = outer.difference(inner) if inner is not None else outer
    if isinstance(shape, Polygon):
        shape = MultiPolygon([shape])
    if shape.is_empty or not shape.is_valid:
        raise ValueError(f"Nie udało się zbudować poprawnej geometrii relacji {relation.get('id')}")
    return shape


def element_point(element: dict) -> tuple[float, float] | None:
    """(lon, lat) of a node or the Overpass `center` of a way/relation."""
    if element["type"] == "node":
        return element["lon"], element["lat"]
    center = element.get("center")
    return (center["lon"], center["lat"]) if center else None
