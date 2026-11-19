import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pipeline.migrate import apply_migrations

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "city_boundary", "services", "land_cover", "analysis_cells",
    "cell_features", "ml_runs", "cluster_assignments", "cluster_statistics", "accessibility_results",
}


def test_migrations_create_schema(db):
    with db.connect() as connection:
        tables = set(connection.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )).scalars())
        gist = set(connection.execute(text(
            "SELECT indexname FROM pg_indexes WHERE indexdef ILIKE '%USING gist%'"
        )).scalars())

    assert EXPECTED_TABLES <= tables
    assert not {"data_imports", "grid_versions", "feature_versions", "assessment_versions"} & tables
    assert {"services_geometry_gix", "analysis_cells_geometry_gix", "city_boundary_geometry_gix"} <= gist


def test_migrations_are_idempotent(db):
    assert apply_migrations(db) == []


def test_services_reject_unknown_category(db):
    insert_service = text(
        "INSERT INTO services (name, category, geometry, inside_city) "
        "VALUES ('SYNTHETIC invalid service', 'invalid_category', ST_SetSRID(ST_MakePoint(0, 0), 2180), true)"
    )
    with pytest.raises(IntegrityError, match="check constraint"):
        with db.begin() as connection:
            connection.execute(insert_service)
