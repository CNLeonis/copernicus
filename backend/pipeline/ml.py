"""Offline K-Means and HDBSCAN experiments over versioned GIS features."""

import argparse
import hashlib
import itertools
import json
from importlib.metadata import PackageNotFoundError, version

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import RobustScaler, StandardScaler
from sqlalchemy import Engine, text

from app.core.config import FEATURE_COLUMNS, get_settings
from app.database import get_engine

ML_METHOD_VERSION = 1
EXPERIMENT_NAME = "baseline-v1"
COUNT_FEATURES = set(FEATURE_COLUMNS[3:])
DEFAULT_K_VALUES = tuple(range(2, 9))
DEFAULT_MIN_CLUSTER_SIZES = (15, 30, 60)
DEFAULT_MIN_SAMPLES = (5, 15)
DEFAULT_SCALERS = ("standard", "robust")
DEFAULT_RANDOM_STATE = 42


def library_versions() -> dict[str, str]:
    try:
        installed_sklearn = version("scikit-learn")
    except PackageNotFoundError:
        installed_sklearn = sklearn_version
    return {
        "scikit-learn": sklearn_version,
        "numpy": np.__version__,
        "hdbscan_implementation": "sklearn.cluster.HDBSCAN",
        "hdbscan_package": installed_sklearn,
    }


def select_complete_rows(rows: list[dict], sample_filter: str) -> tuple[list[dict], dict]:
    """Filter the analysis sample; never impute missing or invalid feature values."""
    if sample_filter not in {"all", "potentially_residential"}:
        raise ValueError("sample_filter must be 'all' or 'potentially_residential'")
    included = [row for row in rows if row["analysis_status"] == "included"]
    sample = included if sample_filter == "all" else [
        row for row in included if row["land_use_status"] == "potentially_residential"
    ]
    complete, missing, non_finite = [], [], []
    for row in sample:
        values = [row.get(name) for name in FEATURE_COLUMNS]
        if any(value is None for value in values):
            missing.append(row)
            continue
        values = np.asarray(values, dtype=float)
        if not np.isfinite(values).all():
            non_finite.append(row)
            continue
        if (values < 0).any():
            raise ValueError(f"Negative accessibility feature in cell {row['cell_id']}")
        complete.append(row)
    counts = {
        "grid_cells": len(rows),
        "excluded_sliver": sum(row["analysis_status"] != "included" for row in rows),
        "included_cells": len(included),
        "sample_filter_excluded": len(included) - len(sample),
        "missing_feature_excluded": len(missing),
        "non_finite_feature_excluded": len(non_finite),
        "n_samples": len(complete),
    }
    if len(complete) < 3:
        raise ValueError(f"At least 3 complete observations are required; found {len(complete)}")
    return complete, counts


def feature_matrix(rows: list[dict]) -> np.ndarray:
    """Apply log1p to counts only; retain distance features in metres before scaling."""
    matrix = np.asarray([[row[name] for name in FEATURE_COLUMNS] for row in rows], dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_COLUMNS):
        raise ValueError("Feature matrix must contain all nine configured features")
    if not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("Feature matrix must contain finite, nonnegative values")
    for index, name in enumerate(FEATURE_COLUMNS):
        if name in COUNT_FEATURES:
            matrix[:, index] = np.log1p(matrix[:, index])
    return matrix


def scale_matrix(matrix: np.ndarray, scaler_name: str) -> np.ndarray:
    scalers = {"standard": StandardScaler, "robust": RobustScaler}
    if scaler_name not in scalers:
        raise ValueError(f"Unsupported scaler: {scaler_name}")
    scaled = scalers[scaler_name]().fit_transform(matrix)
    if not np.isfinite(scaled).all():
        raise ValueError("Scaled feature matrix contains NaN or infinity")
    return scaled


def feature_diagnostics(matrix: np.ndarray) -> dict:
    """Report Tukey-IQR outlier counts after log1p transformation, without dropping rows."""
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_COLUMNS) or not np.isfinite(matrix).all():
        raise ValueError("Diagnostics require a finite matrix with all nine features")
    features = {}
    for index, name in enumerate(FEATURE_COLUMNS):
        values = matrix[:, index]
        q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
        iqr = q3 - q1
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        features[name] = {
            "q1": float(q1), "median": float(median), "q3": float(q3),
            "min": float(values.min()), "max": float(values.max()),
            "iqr_fence_lower": float(lower), "iqr_fence_upper": float(upper),
            "outlier_count": int(((values < lower) | (values > upper)).sum()),
        }
    return {"rule": "Tukey 1.5*IQR after log1p on counts; diagnostic only, no rows removed",
            "features": features}


def cluster_metrics(matrix: np.ndarray, labels: np.ndarray, *, noise_label: int | None = None) -> dict:
    """Evaluate metrics only when at least two nontrivial clusters are available."""
    labels = np.asarray(labels, dtype=int)
    evaluated = np.ones(len(labels), dtype=bool) if noise_label is None else labels != noise_label
    scored_matrix, scored_labels = matrix[evaluated], labels[evaluated]
    cluster_count = len(np.unique(scored_labels))
    valid = len(scored_labels) >= 3 and 2 <= cluster_count < len(scored_labels)
    result = {"silhouette": None, "davies_bouldin": None, "calinski_harabasz": None}
    if valid:
        result["silhouette"] = float(silhouette_score(scored_matrix, scored_labels))
        result["davies_bouldin"] = float(davies_bouldin_score(scored_matrix, scored_labels))
        result["calinski_harabasz"] = float(calinski_harabasz_score(scored_matrix, scored_labels))
    if noise_label is not None:
        result["evaluated_fraction"] = float(evaluated.mean())
    return result


def cluster_statistics(rows: list[dict], labels: np.ndarray) -> list[dict]:
    """Summarize original GIS features, preserving HDBSCAN's -1 noise group."""
    groups: dict[int, list[dict]] = {}
    for row, label in zip(rows, labels, strict=True):
        groups.setdefault(int(label), []).append(row)
    summaries = []
    for label, group in sorted(groups.items()):
        feature_stats = {}
        for column in FEATURE_COLUMNS:
            values = np.asarray([row[column] for row in group if row[column] is not None], dtype=float)
            missing = len(group) - len(values)
            feature_stats[column] = {
                "median": float(np.median(values)) if len(values) else None,
                "q1": float(np.quantile(values, 0.25)) if len(values) else None,
                "q3": float(np.quantile(values, 0.75)) if len(values) else None,
                "min": float(values.min()) if len(values) else None,
                "max": float(values.max()) if len(values) else None,
                "missing": missing,
            }
        summaries.append({
            "cluster_label": label,
            "cell_count": len(group),
            "area_m2": float(sum(row["area_m2"] for row in group)),
            "sample_share": len(group) / len(rows),
            "feature_statistics": feature_stats,
        })
    return summaries


def build_experiment_specs(
    algorithm: str,
    *,
    scalers: tuple[str, ...] = DEFAULT_SCALERS,
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
    min_cluster_sizes: tuple[int, ...] = DEFAULT_MIN_CLUSTER_SIZES,
    min_samples: tuple[int, ...] = DEFAULT_MIN_SAMPLES,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> list[dict]:
    specs = []
    if algorithm in {"all", "kmeans"}:
        for scaler_name, k in itertools.product(scalers, k_values):
            if k >= 2:
                specs.append({
                    "algorithm": "kmeans", "scaler_name": scaler_name,
                    "parameters": {"n_clusters": k, "init": "k-means++", "n_init": 20,
                                   "max_iter": 300, "random_state": random_state},
                    "random_state": random_state,
                })
    if algorithm in {"all", "hdbscan"}:
        for scaler_name, min_size, min_sample in itertools.product(scalers, min_cluster_sizes, min_samples):
            if min_size >= 2 and min_sample >= 1:
                specs.append({
                    "algorithm": "hdbscan", "scaler_name": scaler_name,
                    "parameters": {"min_cluster_size": min_size, "min_samples": min_sample,
                                   "metric": "euclidean", "cluster_selection_method": "eom",
                                   "allow_single_cluster": False},
                    "notes": {"min_samples_convention": "includes_the_point_itself"},
                    "random_state": None,
                })
    if not specs:
        raise ValueError(f"No valid experiment specifications for algorithm={algorithm!r}")
    return specs


def run_id(feature_version: str, config: dict, versions: dict[str, str]) -> str:
    payload = {"method_version": ML_METHOD_VERSION, "feature_version": feature_version,
               "config": config, "library_versions": versions}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    prefix = "km" if config["algorithm"] == "kmeans" else "hb"
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _load_dataset(engine: Engine, grid_version: str | None, feature_version: str | None) -> dict:
    with engine.connect() as connection:
        version_row = connection.execute(text(
            "SELECT f.feature_version, f.grid_version, f.import_id, d.data_version, g.cell_size_m "
            "FROM feature_versions f JOIN grid_versions g USING (grid_version) "
            "JOIN data_imports d ON d.id = f.import_id "
            "WHERE (CAST(:feature AS text) IS NULL OR f.feature_version = :feature) "
            "AND (CAST(:grid AS text) IS NULL OR f.grid_version = :grid) "
            "ORDER BY CASE WHEN CAST(:grid AS text) IS NULL AND g.cell_size_m = 250 THEN 0 ELSE 1 END, "
            "f.computed_at DESC LIMIT 1"
        ), {"grid": grid_version, "feature": feature_version}).mappings().first()
        if version_row is None:
            raise ValueError("No feature version matches the requested grid/version")
        columns = ", ".join(f"cf.{name}" for name in FEATURE_COLUMNS)
        rows = connection.execute(text(
            "SELECT c.id AS cell_id, c.area_m2, c.analysis_status, c.land_use_status, " + columns +
            " FROM analysis_cells c LEFT JOIN cell_features cf "
            "ON cf.cell_id = c.id AND cf.feature_version = :feature "
            "WHERE c.grid_version = :grid ORDER BY c.id"
        ), {"grid": version_row["grid_version"], "feature": version_row["feature_version"]}).mappings().all()
    return {"version": dict(version_row), "rows": [dict(row) for row in rows]}


def _run_config(spec: dict, sample_filter: str, sample_counts: dict) -> dict:
    return {
        "experiment": EXPERIMENT_NAME,
        "algorithm": spec["algorithm"],
        "parameters": spec["parameters"],
        "notes": spec.get("notes", {}),
        "sample_filter": sample_filter,
        "feature_set": "all_nine_accessibility_features",
        "feature_names": list(FEATURE_COLUMNS),
        "transform": "log1p_counts; distances_unchanged",
        "scaler_name": spec["scaler_name"],
        "random_state": spec["random_state"],
        "method_version": ML_METHOD_VERSION,
        "sample_counts": sample_counts,
    }


def _fit(matrix: np.ndarray, spec: dict) -> np.ndarray:
    if spec["algorithm"] == "kmeans":
        model = KMeans(**spec["parameters"])
    else:
        model = HDBSCAN(**spec["parameters"])
    return model.fit_predict(matrix).astype(int)


def _persist_run(
    engine: Engine,
    *,
    experiment_id: str,
    version_info: dict,
    rows: list[dict],
    config: dict,
    labels: np.ndarray,
    metrics: dict,
    statistics: list[dict],
    diagnostics: dict,
    versions: dict[str, str],
    replace: bool,
) -> bool:
    assignments = [
        {"run_id": experiment_id, "cell_id": row["cell_id"], "cluster_label": int(label)}
        for row, label in zip(rows, labels, strict=True)
    ]
    n_clusters = len(set(int(label) for label in labels) - {-1})
    noise_fraction = float(np.mean(labels == -1)) if config["algorithm"] == "hdbscan" else 0.0
    stored_parameters = {key: config[key] for key in (
        "parameters", "notes", "sample_filter", "feature_set", "transform", "sample_counts", "method_version"
    )}
    stored_parameters["validation_diagnostics"] = diagnostics

    with engine.begin() as connection:
        exists = connection.execute(text("SELECT 1 FROM ml_runs WHERE id = :id"),
                                    {"id": experiment_id}).scalar()
        if exists and not replace:
            return False
        if exists:
            connection.execute(text("DELETE FROM ml_runs WHERE id = :id"), {"id": experiment_id})
        connection.execute(text(
            "INSERT INTO ml_runs (id, algorithm, experiment, parameters, feature_version, grid_version, "
            "sample_filter, feature_set, feature_names, transform, scaler_name, random_state, n_samples, "
            "n_clusters, noise_fraction, metrics, library_versions, is_published, artifact_path) "
            "VALUES (:id, :algorithm, :experiment, CAST(:parameters AS jsonb), :feature_version, :grid_version, "
            ":sample_filter, :feature_set, :feature_names, :transform, :scaler_name, :random_state, :n_samples, "
            ":n_clusters, :noise_fraction, CAST(:metrics AS jsonb), CAST(:library_versions AS jsonb), false, NULL)"
        ), {
            "id": experiment_id, "algorithm": config["algorithm"], "experiment": config["experiment"],
            "parameters": json.dumps(stored_parameters, sort_keys=True),
            "feature_version": version_info["feature_version"], "grid_version": version_info["grid_version"],
            "sample_filter": config["sample_filter"], "feature_set": config["feature_set"],
            "feature_names": config["feature_names"], "transform": config["transform"],
            "scaler_name": config["scaler_name"], "random_state": config["random_state"],
            "n_samples": len(assignments), "n_clusters": n_clusters, "noise_fraction": noise_fraction,
            "metrics": json.dumps(metrics, allow_nan=False),
            "library_versions": json.dumps(versions, sort_keys=True),
        })
        connection.execute(text(
            "INSERT INTO cluster_assignments (run_id, cell_id, cluster_label) "
            "VALUES (:run_id, :cell_id, :cluster_label)"
        ), assignments)
        connection.execute(text(
            "INSERT INTO cluster_statistics (run_id, cluster_label, cell_count, area_m2, sample_share, feature_statistics) "
            "VALUES (:run_id, :cluster_label, :cell_count, :area_m2, :sample_share, CAST(:feature_statistics AS jsonb))"
        ), [{
            "run_id": experiment_id, "cluster_label": item["cluster_label"],
            "cell_count": item["cell_count"], "area_m2": item["area_m2"],
            "sample_share": item["sample_share"],
            "feature_statistics": json.dumps(item["feature_statistics"], allow_nan=False),
        } for item in statistics])
    return True


def run(
    *,
    grid_version: str | None = None,
    feature_version: str | None = None,
    sample_filter: str = "all",
    algorithm: str = "all",
    scalers: tuple[str, ...] = DEFAULT_SCALERS,
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
    min_cluster_sizes: tuple[int, ...] = DEFAULT_MIN_CLUSTER_SIZES,
    min_samples: tuple[int, ...] = DEFAULT_MIN_SAMPLES,
    random_state: int = DEFAULT_RANDOM_STATE,
    replace: bool = False,
    engine: Engine | None = None,
) -> dict:
    engine = engine or get_engine()
    dataset = _load_dataset(engine, grid_version, feature_version)
    rows, sample_counts = select_complete_rows(dataset["rows"], sample_filter)
    matrix = feature_matrix(rows)
    diagnostics = feature_diagnostics(matrix)
    versions = library_versions()
    specs = build_experiment_specs(
        algorithm, scalers=scalers, k_values=k_values,
        min_cluster_sizes=min_cluster_sizes, min_samples=min_samples,
        random_state=random_state,
    )
    outputs = []

    for spec in specs:
        if spec["algorithm"] == "kmeans" and spec["parameters"]["n_clusters"] >= len(rows):
            continue
        scaled = scale_matrix(matrix, spec["scaler_name"])
        labels = _fit(scaled, spec)
        noise_label = -1 if spec["algorithm"] == "hdbscan" else None
        metrics = cluster_metrics(scaled, labels, noise_label=noise_label)
        config = _run_config(spec, sample_filter, sample_counts)
        identifier = run_id(dataset["version"]["feature_version"], config, versions)
        statistics = cluster_statistics(rows, labels)
        written = _persist_run(
            engine, experiment_id=identifier, version_info=dataset["version"], rows=rows,
            config=config, labels=labels, metrics=metrics, statistics=statistics,
            diagnostics=diagnostics, versions=versions, replace=replace,
        )
        outputs.append({
            "id": identifier,
            "algorithm": spec["algorithm"],
            "parameters": spec["parameters"],
            "scaler_name": spec["scaler_name"],
            "n_samples": len(rows),
            "n_clusters": len(set(labels.tolist()) - {-1}),
            "noise_fraction": float(np.mean(labels == -1)) if noise_label is not None else 0.0,
            "metrics": metrics,
            "written": written,
        })

    return {
        "feature_version": dataset["version"]["feature_version"],
        "grid_version": dataset["version"]["grid_version"],
        "data_version": dataset["version"]["data_version"],
        "sample_filter": sample_filter,
        "sample_counts": sample_counts,
        "data_diagnostics": diagnostics,
        "library_versions": versions,
        "runs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-version", help="grid version (default: latest with 250 m priority)")
    parser.add_argument("--feature-version", help="feature version (default: latest for the selected grid)")
    parser.add_argument("--sample-filter", choices=("all", "potentially_residential"), default="all")
    parser.add_argument("--algorithm", choices=("all", "kmeans", "hdbscan"), default="all")
    parser.add_argument("--scalers", nargs="+", choices=("standard", "robust"), default=list(DEFAULT_SCALERS))
    parser.add_argument("--k-values", nargs="+", type=int, default=list(DEFAULT_K_VALUES))
    parser.add_argument("--min-cluster-sizes", nargs="+", type=int, default=list(DEFAULT_MIN_CLUSTER_SIZES))
    parser.add_argument("--min-samples", nargs="+", type=int, default=list(DEFAULT_MIN_SAMPLES))
    parser.add_argument("--random-state", type=int, default=DEFAULT_RANDOM_STATE)
    parser.add_argument("--replace", action="store_true", help="replace matching runs and cascading dependent data")
    args = parser.parse_args()
    report = run(
        grid_version=args.grid_version, feature_version=args.feature_version,
        sample_filter=args.sample_filter, algorithm=args.algorithm, scalers=tuple(args.scalers),
        k_values=tuple(args.k_values), min_cluster_sizes=tuple(args.min_cluster_sizes),
        min_samples=tuple(args.min_samples), random_state=args.random_state, replace=args.replace,
    )
    reports_dir = get_settings().data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"ml_{report['feature_version']}_{report['sample_filter']}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
