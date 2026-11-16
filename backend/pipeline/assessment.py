"""Apply versioned, fixed accessibility thresholds to saved GIS features.

Usage:
    python -m pipeline.assessment --feature-version VERSION

This step reads existing feature rows only. It does not import OSM, compute
features, or train ML models. Pass the chosen feature version explicitly.
"""

import argparse
import hashlib
import json
from collections import Counter
from numbers import Integral

from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.database import get_engine

METHOD_VERSION = 1
RADIUS_GOOD_M = 500
RADIUS_LIMIT_M = 1000
SAMPLE_FILTERS = ("all", "potentially_residential")
CATEGORIES = {
    "shop": ("shops_500m", "shops_1000m"),
    "school": ("schools_500m", "schools_1000m"),
    "bus_stop": ("bus_stops_500m", "bus_stops_1000m"),
}
ACCESSIBILITY_SCORE = {"high": 1.0, "medium": 0.5, "low": 0.0}


def classify_category(count_500m: int | None, count_1000m: int | None) -> str:
    """Classify one service from reliable inclusive-radius counts."""
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


def assess_row(row: dict, sample_filter: str) -> dict:
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
    low_count = sum(value == "low" for value in category_classes.values())
    return {
        "cell_id": row["cell_id"],
        "accessibility_class": overall,
        "accessibility_score": score,
        "criteria_results": {
            "method": "fixed_radius_counts",
            "sample_filter": sample_filter,
            "categories": categories,
            "low_category_count": low_count,
            "overall_rule": "low when at least two categories are low; medium when exactly one is low or at least two are medium; otherwise high when all three are known",
            "partial_data_note": (
                "At least two known low categories determine low overall access despite another missing category."
                if low_count >= 2 and "no_data" in category_classes.values()
                else None
            ),
        },
    }


def assessment_version(feature_version: str, sample_filter: str) -> str:
    payload = {
        "method_version": METHOD_VERSION,
        "feature_version": feature_version,
        "sample_filter": sample_filter,
        "radii_m": [RADIUS_GOOD_M, RADIUS_LIMIT_M],
        "category_columns": CATEGORIES,
        "category_score": ACCESSIBILITY_SCORE,
        "overall_rule": "low>=2; medium=low==1 or medium>=2; high otherwise if complete",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"as-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _load_dataset(engine: Engine, feature_version: str, grid_version: str | None) -> dict:
    with engine.connect() as connection:
        version = connection.execute(text(
            "SELECT fv.feature_version, fv.grid_version, fv.import_id, di.data_version, "
            "gv.cell_size_m, COALESCE(NULLIF(di.validation_report->>'buffer_m', '')::double precision, 0) buffer_m "
            "FROM feature_versions fv JOIN grid_versions gv USING (grid_version) "
            "JOIN data_imports di ON di.id = fv.import_id "
            "WHERE fv.feature_version = :feature "
            "AND (CAST(:grid AS text) IS NULL OR fv.grid_version = :grid)"
        ), {"feature": feature_version, "grid": grid_version}).mappings().first()
        if version is None:
            raise ValueError("No feature version matches the requested grid/version")

        columns = ", ".join(f"cf.{name}" for pair in CATEGORIES.values() for name in pair)
        rows = connection.execute(text(
            "SELECT c.id AS cell_id, c.analysis_status, c.land_use_status, " + columns +
            " FROM analysis_cells c LEFT JOIN cell_features cf "
            "ON cf.cell_id = c.id AND cf.feature_version = :feature "
            "WHERE c.grid_version = :grid ORDER BY c.id"
        ), {"grid": version["grid_version"], "feature": feature_version}).mappings().all()
    return {"version": dict(version), "rows": [dict(row) for row in rows]}


def _selected_rows(rows: list[dict], sample_filter: str) -> tuple[list[dict], dict]:
    if sample_filter not in SAMPLE_FILTERS:
        raise ValueError("sample_filter must be 'all' or 'potentially_residential'")
    included = [row for row in rows if row["analysis_status"] == "included"]
    selected = included if sample_filter == "all" else [
        row for row in included if row["land_use_status"] == "potentially_residential"
    ]
    return selected, {
        "grid_cells": len(rows),
        "excluded_sliver": len(rows) - len(included),
        "included_cells": len(included),
        "sample_filter_excluded": len(included) - len(selected),
        "assessed_cells": len(selected),
    }


def _parameters(feature_info: dict, sample_filter: str) -> dict:
    return {
        "method_version": METHOD_VERSION,
        "sample_filter": sample_filter,
        "radii_m": {"good_max": RADIUS_GOOD_M, "low_if_zero_within": RADIUS_LIMIT_M},
        "category_columns": CATEGORIES,
        "category_score": ACCESSIBILITY_SCORE,
        "score_definition": "mean of category scores: high=1, medium=0.5, low=0; null if any category is no_data",
        "overall_rule": "low when at least two categories are low; medium when exactly one is low or at least two are medium; high otherwise when all three are known; no_data for incomplete data unless two categories are low",
        "grid_version": feature_info["grid_version"],
        "cell_size_m": feature_info["cell_size_m"],
        "data_version": feature_info["data_version"],
        "source_buffer_m": feature_info["buffer_m"],
    }


def _persist(
    engine: Engine,
    *,
    version: str,
    feature_info: dict,
    sample_filter: str,
    parameters: dict,
    results: list[dict],
    replace: bool,
) -> bool:
    with engine.begin() as connection:
        exists = connection.execute(text(
            "SELECT 1 FROM assessment_versions WHERE assessment_version = :version"
        ), {"version": version}).scalar()
        if exists and not replace:
            return False
        if exists:
            connection.execute(text(
                "DELETE FROM assessment_versions WHERE assessment_version = :version"
            ), {"version": version})
        connection.execute(text(
            "INSERT INTO assessment_versions (assessment_version, feature_version, variant, parameters, description) "
            "VALUES (:version, :feature, 'threshold', CAST(:parameters AS jsonb), :description)"
        ), {
            "version": version,
            "feature": feature_info["feature_version"],
            "parameters": json.dumps(parameters, ensure_ascii=False, sort_keys=True),
            "description": (
                "Stała ocena dostępności: "
                + ("wszystkie komórki uwzględnione w siatce" if sample_filter == "all"
                   else "komórki potencjalnie mieszkaniowe")
                + "; progi 500 m i 1000 m."
            ),
        })
        if results:
            connection.execute(text(
                "INSERT INTO accessibility_results "
                "(cell_id, assessment_version, accessibility_score, accessibility_class, criteria_results) "
                "VALUES (:cell_id, :assessment_version, :accessibility_score, :accessibility_class, "
                "CAST(:criteria_results AS jsonb))"
            ), [{
                **result,
                "assessment_version": version,
                "criteria_results": json.dumps(result["criteria_results"], ensure_ascii=False, allow_nan=False),
            } for result in results])
    return True


def run(
    *,
    feature_version: str,
    grid_version: str | None = None,
    sample_filter: str = "potentially_residential",
    replace: bool = False,
    engine: Engine | None = None,
) -> dict:
    if not feature_version:
        raise ValueError("An explicit feature_version is required")
    if sample_filter not in SAMPLE_FILTERS:
        raise ValueError("sample_filter must be 'all' or 'potentially_residential'")
    engine = engine or get_engine()
    dataset = _load_dataset(engine, feature_version, grid_version)
    selected, sample_counts = _selected_rows(dataset["rows"], sample_filter)
    results = [assess_row(row, sample_filter) for row in selected]
    version = assessment_version(feature_version, sample_filter)
    parameters = _parameters(dataset["version"], sample_filter)
    written = _persist(
        engine, version=version, feature_info=dataset["version"],
        sample_filter=sample_filter, parameters=parameters, results=results, replace=replace,
    )
    class_counts = Counter(result["accessibility_class"] for result in results)
    return {
        "assessment_version": version,
        "feature_version": feature_version,
        "grid_version": dataset["version"]["grid_version"],
        "data_version": dataset["version"]["data_version"],
        "sample_filter": sample_filter,
        "sample_counts": sample_counts,
        "class_counts": dict(sorted(class_counts.items())),
        "written": written,
        "parameters": parameters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-version", required=True,
                        help="feature version to assess (required; never choose latest implicitly)")
    parser.add_argument("--grid-version", help="optional grid version consistency check")
    parser.add_argument("--sample-filter", choices=SAMPLE_FILTERS, default="potentially_residential")
    parser.add_argument("--replace", action="store_true", help="replace this assessment version and its results")
    args = parser.parse_args()
    report = run(
        feature_version=args.feature_version,
        grid_version=args.grid_version,
        sample_filter=args.sample_filter,
        replace=args.replace,
    )
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"assessment_{report['assessment_version']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
