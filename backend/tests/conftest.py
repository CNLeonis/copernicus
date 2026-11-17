"""Shared fixtures.

Tests marked `db` use a separate PostGIS database (TEST_DATABASE_URL). Without it they are
skipped locally; with REQUIRE_DB=1 (CI) a missing database is an error.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.database import get_connection
from app.main import app
from pipeline.migrate import apply_migrations

DEFAULT_TEST_URL = "postgresql+psycopg://copernicus:copernicus@localhost:5432/copernicus_test"

DATA_TABLES = (
    "accessibility_results, cluster_statistics, cluster_assignments, ml_runs, "
    "cell_features, analysis_cells, land_cover, services, city_boundary"
)


def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            ).first()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def db_engine():
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)
    try:
        _create_database_if_missing(url)
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    except Exception as error:  # noqa: BLE001 - any connection problem means "no test DB"
        if os.environ.get("REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Brak testowej bazy PostGIS ({error.__class__.__name__})")
    apply_migrations(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    yield db_engine
    with db_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {DATA_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_client(db):
    def override():
        with db.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
