"""Apply fixed accessibility rules to current cell features offline.

Usage: python -m pipeline.assessment [--size 250] [--replace]
"""

import argparse
import json
from collections import Counter
from numbers import Integral

from sqlalchemy import Engine, text

from app.database import get_engine

CATEGORIES = {
    "shop": ("shops_500m", "shops_1000m"),
    "school": ("schools_500m", "schools_1000m"),
    "bus_stop": ("bus_stops_500m", "bus_stops_1000m"),
}
ACCESSIBILITY_SCORE = {"high": 1.0, "medium": 0.5, "low": 0.0}


def classify_category(count_500m: int | None, count_1000m: int | None) -> str:
    """Classify one service from reliable inclusive radius counts."""
    for value in (count_500m, count_1000m):
        if value is not None and (isinstance(value, bool) or not isinstance(value, Integral) or value < 0):
            raise ValueError("Service counts must be nonnegative integers or None")
    if count_500m is None or count_1000m is None:
        return "no_data"
    if count_500m > count_1000m:
        raise ValueError("The 500 m count cannot exceed the 1000 m count")
    if count_500m >= 1:
        return "high"
    if count_1000m >= 1:
        return "medium"
    return "low"


def classify_overall(category_classes: dict[str, str]) -> tuple[str, float | None]:
    """Combine three category states; two low categories are sufficient evidence."""
    if set(category_classes) != set(CATEGORIES):
        raise ValueError("Overall classification requires shop, school, and bus_stop")
    allowed = {"high", "medium", "low", "no_data"}
    if any(value not in allowed for value in category_classes.values()):
        raise ValueError("Unknown service accessibility class")

    values = list(category_classes.values())
    low_count = values.count("low")
    medium_count = values.count("medium")
    if low_count >= 2:
        overall = "low"
    elif "no_data" in values:
        overall = "no_data"
    elif low_count == 1 or medium_count >= 2:
        overall = "medium"
    else:
        overall = "high"

    score = (
        sum(ACCESSIBILITY_SCORE[value] for value in values) / len(values)
        if "no_data" not in values else None
    )
    return overall, score


def assess_row(row: dict) -> dict:
    categories = {}
    for category, (count_500_column, count_1000_column) in CATEGORIES.items():
        count_500m = row.get(count_500_column)
        count_1000m = row.get(count_1000_column)
        categories[category] = {
            "class": classify_category(count_500m, count_1000m),
            "count_500m": count_500m,
            "count_1000m": count_1000m,
        }

    category_classes = {name: result["class"] for name, result in categories.items()}
    overall, score = classify_overall(category_classes)
    return {
        "cell_id": row["cell_id"],
        "accessibility_class": overall,
        "accessibility_score": score,
        "criteria_results": {
            "categories": categories,
            "partial_data_note": (
                "At least two known low categories determine low overall access despite another missing category."
                if list(category_classes.values()).count("low") >= 2 and "no_data" in category_classes.values()
                else None
            ),
        },
    }


def _load_rows(engine: Engine, cell_size_m: int) -> list[dict]:
    columns = ", ".join(f"f.{name}" for pair in CATEGORIES.values() for name in pair)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.id AS cell_id, " + columns +
            " FROM analysis_cells c LEFT JOIN cell_features f ON f.cell_id = c.id "
            "WHERE c.cell_size_m = :size AND c.analysis_status = 'included' ORDER BY c.id"
        ), {"size": cell_size_m}).mappings().all()
    if not rows:
        raise ValueError(f"No included analysis cells for {cell_size_m} m grid")
    return [dict(row) for row in rows]


def _persist(engine: Engine, cell_size_m: int, results: list[dict], replace: bool) -> bool:
    with engine.begin() as connection:
        existing = connection.execute(text(
            "SELECT 1 FROM accessibility_results ar JOIN analysis_cells c ON c.id = ar.cell_id "
            "WHERE c.cell_size_m = :size LIMIT 1"
        ), {"size": cell_size_m}).scalar()
        if existing and not replace:
            return False
        if existing:
            connection.execute(text(
                "DELETE FROM accessibility_results ar USING analysis_cells c "
                "WHERE c.id = ar.cell_id AND c.cell_size_m = :size"
            ), {"size": cell_size_m})
        connection.execute(text(
            "INSERT INTO accessibility_results "
            "(cell_id, accessibility_score, accessibility_class, criteria_results) "
            "VALUES (:cell_id, :accessibility_score, :accessibility_class, CAST(:criteria_results AS jsonb))"
        ), [{**result, "criteria_results": json.dumps(result["criteria_results"], ensure_ascii=False)}
            for result in results])
    return True


def run(*, cell_size_m: int = 250, replace: bool = False, engine: Engine | None = None) -> dict:
    engine = engine or get_engine()
    results = [assess_row(row) for row in _load_rows(engine, cell_size_m)]
    written = _persist(engine, cell_size_m, results, replace)
    return {
        "cell_size_m": cell_size_m,
        "assessed_cells": len(results),
        "class_counts": dict(sorted(Counter(row["accessibility_class"] for row in results).items())),
        "written": written,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, choices=(250, 500), default=250)
    parser.add_argument("--replace", action="store_true", help="recompute saved assessments")
    args = parser.parse_args()
    print(json.dumps(run(cell_size_m=args.size, replace=args.replace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
