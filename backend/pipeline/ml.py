"""Offline K-Means and HDBSCAN experiments over current GIS features."""

import argparse
import hashlib
import json

import numpy as np
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import RobustScaler, StandardScaler
from sqlalchemy import Engine, text

from app.core.config import FEATURE_COLUMNS
from app.database import get_engine

COUNT_COLUMNS = FEATURE_COLUMNS[3:]
COUNT_FEATURES = set(COUNT_COLUMNS)
DEFAULT_K_VALUES = tuple(range(2, 9))
HDBSCAN_MIN_CLUSTER_SIZE = 30
HDBSCAN_MIN_SAMPLES = 10
DEFAULT_RANDOM_STATE = 42


def select_complete_rows(
    rows: list[dict], sample_filter: str, feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
) -> tuple[list[dict], dict]:
    """Filter the analysis sample; never impute missing or invalid feature values."""
    if sample_filter not in {"all", "potentially_residential"}:
        raise ValueError("sample_filter must be 'all' or 'potentially_residential'")
    included = [row for row in rows if row["analysis_status"] == "included"]
    sample = included if sample_filter == "all" else [
        row for row in included if row["land_use_status"] == "potentially_residential"
    ]
    complete, missing, non_finite = [], [], []
    for row in sample:
        values = [row.get(name) for name in feature_columns]
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


def feature_matrix(rows: list[dict], feature_columns: tuple[str, ...] = FEATURE_COLUMNS) -> np.ndarray:
    """Apply log1p to counts only; retain distance features in metres before scaling."""
    matrix = np.asarray([[row[name] for name in feature_columns] for row in rows], dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(feature_columns):
        raise ValueError("Feature matrix has an unexpected number of columns")
    if not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("Feature matrix must contain finite, nonnegative values")
    for index, name in enumerate(feature_columns):
        if name in COUNT_FEATURES:
            matrix[:, index] = np.log1p(matrix[:, index])
    return matrix


def scale_matrix(matrix: np.ndarray, scaler_name: str = "standard") -> np.ndarray:
    scaler = {"standard": StandardScaler, "robust": RobustScaler}.get(scaler_name)
    if scaler is None:
        raise ValueError(f"Unknown scaler: {scaler_name}")
    scaled = scaler().fit_transform(matrix)
    if not np.isfinite(scaled).all():
        raise ValueError("Scaled feature matrix contains NaN or infinity")
    return scaled


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
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> list[dict]:
    if algorithm not in {"all", "kmeans", "hdbscan"}:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    specs = []
    if algorithm in {"all", "kmeans"}:
        for k in k_values:
            if k >= 2:
                specs.append({
                    "algorithm": "kmeans", "scaler_name": "standard",
                    "parameters": {"n_clusters": k, "init": "k-means++", "n_init": 20,
                                   "max_iter": 300, "random_state": random_state},
                    "random_state": random_state,
                })
    if algorithm in {"all", "hdbscan"}:
        specs.append({
            "algorithm": "hdbscan", "scaler_name": "standard",
            "parameters": {"min_cluster_size": HDBSCAN_MIN_CLUSTER_SIZE,
                           "min_samples": HDBSCAN_MIN_SAMPLES},
            "random_state": None,
        })
    if not specs:
        raise ValueError(f"No valid experiment specifications for algorithm={algorithm!r}")
    return specs


def run_id(cell_size_m: int, config: dict) -> str:
    """Identify one experiment configuration for one grid resolution."""
    payload = {"cell_size_m": cell_size_m, "config": config}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    prefix = "km" if config["algorithm"] == "kmeans" else "hb"
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _load_dataset(engine: Engine, cell_size_m: int) -> list[dict]:
    with engine.connect() as connection:
        columns = ", ".join(f"cf.{name}" for name in FEATURE_COLUMNS)
        rows = connection.execute(text(
            "SELECT c.id AS cell_id, c.area_m2, c.analysis_status, c.land_use_status, " + columns +
            " FROM analysis_cells c LEFT JOIN cell_features cf ON cf.cell_id = c.id "
            "WHERE c.cell_size_m = :size ORDER BY c.id"
        ), {"size": cell_size_m}).mappings().all()
    if not rows:
        raise ValueError(f"No analysis cells for {cell_size_m} m grid")
    return [dict(row) for row in rows]


def _run_config(spec: dict, sample_filter: str, feature_set: str) -> dict:
    config = {
        "algorithm": spec["algorithm"],
        "parameters": spec["parameters"],
        "sample_filter": sample_filter,
        "scaler_name": spec["scaler_name"],
        "random_state": spec["random_state"],
    }
    if feature_set == "counts":
        config["feature_set"] = "counts_six"
    return config


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
    cell_size_m: int,
    rows: list[dict],
    config: dict,
    labels: np.ndarray,
    metrics: dict,
    statistics: list[dict],
    replace: bool,
) -> bool:
    assignments = [
        {"run_id": experiment_id, "cell_id": row["cell_id"], "cluster_label": int(label)}
        for row, label in zip(rows, labels, strict=True)
    ]
    n_clusters = len(set(int(label) for label in labels) - {-1})
    noise_fraction = float(np.mean(labels == -1)) if config["algorithm"] == "hdbscan" else 0.0
    with engine.begin() as connection:
        exists = connection.execute(text("SELECT 1 FROM ml_runs WHERE id = :id"),
                                    {"id": experiment_id}).scalar()
        if exists and not replace:
            return False
        if exists:
            connection.execute(text("DELETE FROM ml_runs WHERE id = :id"), {"id": experiment_id})
        connection.execute(text(
            "INSERT INTO ml_runs (id, algorithm, parameters, cell_size_m, sample_filter, scaler_name, "
            "random_state, n_samples, n_clusters, noise_fraction, metrics, is_published) "
            "VALUES (:id, :algorithm, CAST(:parameters AS jsonb), :cell_size_m, :sample_filter, "
            ":scaler_name, :random_state, :n_samples, :n_clusters, :noise_fraction, "
            "CAST(:metrics AS jsonb), :is_published)"
        ), {
            "id": experiment_id, "algorithm": config["algorithm"],
            "parameters": json.dumps({**config["parameters"], **(
                {"feature_set": config["feature_set"]} if "feature_set" in config else {}
            )}, sort_keys=True),
            "cell_size_m": cell_size_m, "sample_filter": config["sample_filter"],
            "scaler_name": config["scaler_name"], "random_state": config["random_state"],
            "n_samples": len(assignments), "n_clusters": n_clusters, "noise_fraction": noise_fraction,
            "metrics": json.dumps(metrics, allow_nan=False),
            "is_published": "feature_set" in config,
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
    cell_size_m: int = 250,
    sample_filter: str = "all",
    algorithm: str = "kmeans",
    k_values: tuple[int, ...] = (2,),
    feature_set: str = "counts",
    random_state: int = DEFAULT_RANDOM_STATE,
    replace: bool = False,
    engine: Engine | None = None,
) -> dict:
    if feature_set not in {"counts", "all_nine"}:
        raise ValueError(f"Unknown feature set: {feature_set}")
    if feature_set == "counts" and (algorithm != "kmeans" or k_values != (2,)):
        raise ValueError("The six-count default uses K-Means with k=2")
    engine = engine or get_engine()
    columns = COUNT_COLUMNS if feature_set == "counts" else FEATURE_COLUMNS
    rows, sample_counts = select_complete_rows(
        _load_dataset(engine, cell_size_m), sample_filter, columns
    )
    matrix = feature_matrix(rows, columns)
    specs = build_experiment_specs(algorithm, k_values=k_values, random_state=random_state)
    scaler_name = "robust" if feature_set == "counts" and cell_size_m == 500 else "standard"
    scaled = scale_matrix(matrix, scaler_name)
    outputs = []

    for spec in specs:
        spec["scaler_name"] = scaler_name
        if spec["algorithm"] == "kmeans" and spec["parameters"]["n_clusters"] >= len(rows):
            continue
        labels = _fit(scaled, spec)
        noise_label = -1 if spec["algorithm"] == "hdbscan" else None
        metrics = cluster_metrics(scaled, labels, noise_label=noise_label)
        config = _run_config(spec, sample_filter, feature_set)
        identifier = run_id(cell_size_m, config)
        statistics = cluster_statistics(rows, labels)
        written = _persist_run(
            engine, experiment_id=identifier, cell_size_m=cell_size_m, rows=rows,
            config=config, labels=labels, metrics=metrics, statistics=statistics,
            replace=replace,
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
        "cell_size_m": cell_size_m,
        "sample_filter": sample_filter,
        "feature_set": feature_set,
        "sample_counts": sample_counts,
        "runs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, choices=(250, 500), default=250,
                        help="grid cell size in metres (default: 250)")
    parser.add_argument("--sample-filter", choices=("all", "potentially_residential"), default="all")
    parser.add_argument("--algorithm", choices=("all", "kmeans", "hdbscan"), default="kmeans")
    parser.add_argument("--feature-set", choices=("counts", "all_nine"), default="counts")
    parser.add_argument("--k-values", nargs="+", type=int, default=[2])
    parser.add_argument("--random-state", type=int, default=DEFAULT_RANDOM_STATE)
    parser.add_argument("--replace", action="store_true", help="replace matching runs and cascading dependent data")
    args = parser.parse_args()
    report = run(
        cell_size_m=args.size,
        sample_filter=args.sample_filter, algorithm=args.algorithm,
        k_values=tuple(args.k_values), feature_set=args.feature_set,
        random_state=args.random_state, replace=args.replace,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
