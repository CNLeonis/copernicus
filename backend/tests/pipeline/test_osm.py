"""SYNTHETIC fixtures only: geometries and tags below are fictitious."""

import json

import pytest
from pipeline import osm


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
    assert cached == first
    assert "_download" not in cached
