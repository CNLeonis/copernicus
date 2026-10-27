from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

WGS84 = "EPSG:4326"
METRIC_CRS = "EPSG:2180"
METRIC_SRID = 2180

CATEGORIES = ("shop", "school", "bus_stop")
RADII_M = (500, 1000)
GRID_SIZES_M = (250, 500)
BOUNDARY_BUFFER_M = 2000

FEATURE_COLUMNS = (
    "nearest_shop_m",
    "nearest_school_m",
    "nearest_bus_stop_m",
    "shops_500m",
    "shops_1000m",
    "schools_500m",
    "schools_1000m",
    "bus_stops_500m",
    "bus_stops_1000m",
)

OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://copernicus:copernicus@localhost:5432/copernicus"
    data_dir: Path = BACKEND_DIR / "data"
    artifacts_dir: Path = BACKEND_DIR / "artifacts"
    overpass_url: str = "https://overpass-api.de/api/interpreter"


@lru_cache
def get_settings() -> Settings:
    return Settings()
