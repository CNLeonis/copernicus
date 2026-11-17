"""Coordinate transforms shared by source importers."""

import shapely
from pyproj import Transformer

from app.core.config import METRIC_CRS, WGS84


_TO_METRIC = Transformer.from_crs(WGS84, METRIC_CRS, always_xy=True).transform
_TO_WGS84 = Transformer.from_crs(METRIC_CRS, WGS84, always_xy=True).transform


def to_metric(geometry):
    return shapely.transform(geometry, lambda x, y: _TO_METRIC(x, y), interleaved=False)


def to_wgs84(geometry):
    return shapely.transform(geometry, lambda x, y: _TO_WGS84(x, y), interleaved=False)
