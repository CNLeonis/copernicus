"""Apply SQL migrations from backend/db/migrations in filename order.

Usage: python -m pipeline.migrate
"""

from pathlib import Path

from sqlalchemy import Engine, text

from app.core.config import BACKEND_DIR
from app.database import get_engine

MIGRATIONS_DIR = BACKEND_DIR / "db" / "migrations"


def apply_migrations(engine: Engine, directory: Path = MIGRATIONS_DIR) -> list[str]:
    applied_now = []
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        ))
    for path in sorted(directory.glob("*.sql")):
        with engine.begin() as connection:
            done = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": path.name},
            ).first()
            if done:
                continue
            # Raw cursor: migration files contain several statements.
            connection.connection.cursor().execute(path.read_text(encoding="utf-8"))
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": path.name},
            )
        applied_now.append(path.name)
    return applied_now


def main() -> None:
    applied = apply_migrations(get_engine())
    print("Zastosowano migracje: " + (", ".join(applied) if applied else "brak nowych"))


if __name__ == "__main__":
    main()
