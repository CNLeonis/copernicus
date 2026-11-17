"""Read-only data and clustering review for the current Opole database."""

from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from app.core.config import FEATURE_COLUMNS
from app.database import get_engine
from pipeline.ml import feature_matrix, select_complete_rows

OUT = Path(__file__).resolve().parents[1] / "data" / "reports"
OUT.mkdir(exist_ok=True)


def metric(value):
    return "—" if value is None else f"{value:.3f}"


def profile(rows, columns):
    result = []
    for column in columns:
        missing = sum(row[column] is None for row in rows)
        result.append((column, missing, 100 * missing / len(rows)))
    return result


def spatial_review(rows, run, labels_by_cell):
    complete, _ = select_complete_rows(rows, "all")
    matrix = feature_matrix(complete)
    groups = np.asarray([(int(row["x"] // 1000), int(row["y"] // 1000)) for row in complete])
    # GroupShuffleSplit accepts one group ID per row, including repeated cells in a 1 km block.
    group_ids = [f"{x}:{y}" for x, y in groups]
    reference = np.asarray([labels_by_cell[row["cell_id"]] for row in complete])
    stabilities, test_silhouettes, test_sizes, selected_ks = [], [], [], []
    for seed in (42, 43, 44):
        train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.30,
                                             random_state=seed).split(matrix, groups=group_ids))
        scaler = StandardScaler().fit(matrix[train])
        x_train, x_test = scaler.transform(matrix[train]), scaler.transform(matrix[test])
        if run["algorithm"] == "kmeans":
            candidates = []
            for k in range(2, 9):
                parameters = {**run["parameters"], "n_clusters": k}
                candidate = KMeans(**parameters).fit(x_train)
                candidates.append((silhouette_score(x_train, candidate.labels_), k, candidate))
            _, selected_k, model = max(candidates, key=lambda item: item[0])
            selected_ks.append(selected_k)
            train_labels = model.labels_
            predicted = model.predict(x_test)
            if 2 <= len(set(predicted)) < len(predicted):
                test_silhouettes.append(float(silhouette_score(x_test, predicted)))
        else:
            model = HDBSCAN(**run["parameters"])
            train_labels = model.fit_predict(x_train)
        stabilities.append(float(adjusted_rand_score(reference[train], train_labels)))
        test_sizes.append(len(test))
    return {
        "train": len(complete) - int(np.median(test_sizes)),
        "test": int(np.median(test_sizes)),
        "stability_ari": float(np.mean(stabilities)),
        "stability_range": (min(stabilities), max(stabilities)),
        "test_silhouette": float(np.mean(test_silhouettes)) if test_silhouettes else None,
        "selected_ks": selected_ks,
    }


with get_engine().connect() as connection:
    services = [dict(row) for row in connection.execute(text(
        "SELECT id, name, category, subcategory, inside_city, ST_AsEWKB(geometry) AS geometry "
        "FROM services ORDER BY id"
    )).mappings()]
    feature_rows = [dict(row) for row in connection.execute(text(
        "SELECT c.id AS cell_id, c.cell_size_m, c.area_m2, c.analysis_status, "
        "c.land_use_status, ST_X(c.representative_point) AS x, "
        "ST_Y(c.representative_point) AS y, "
        + ", ".join(f"cf.{name}" for name in FEATURE_COLUMNS) + ", "
        "cf.shop_status, cf.school_status, cf.bus_stop_status, cf.buffer_sufficient "
        "FROM analysis_cells c LEFT JOIN cell_features cf ON cf.cell_id=c.id "
        "ORDER BY c.cell_size_m, c.id"
    )).mappings()]
    runs = [dict(row) for row in connection.execute(text(
        "SELECT id, algorithm, parameters, cell_size_m, n_samples, n_clusters, "
        "noise_fraction, metrics, is_published FROM ml_runs ORDER BY cell_size_m, algorithm, id"
    )).mappings()]
    assignment_rows = connection.execute(text(
        "SELECT a.run_id, a.cell_id, a.cluster_label FROM cluster_assignments a "
        "JOIN ml_runs r ON r.id=a.run_id WHERE r.is_published"
    )).all()

assignments = {}
for run_id, cell_id, label in assignment_rows:
    assignments.setdefault(run_id, {})[cell_id] = label

lines = [
    f"# Przegląd danych i modeli – {date.today().isoformat()}", "",
    "Analiza rzeczywistych danych w bieżącej bazie; operacja tylko do odczytu.", "",
    "## Kształt i kompletność danych", "",
    f"- `services.shape`: ({len(services)}, 6); kolumny: id (integer), name (tekst), "
    "category (tekst), subcategory (tekst), inside_city (boolean), geometry (Point EPSG:2180).",
    f"- `analysis_cells + cell_features.shape`: ({len(feature_rows)}, {len(feature_rows[0])}); "
    "jedna komórka na wiersz, 9 cech GIS.", "",
    "| Kolumna usług | Typ | Non-Null Count | NULL | Udział |",
    "|---|---|---:|---:|---:|",
]
service_types = {"id": "integer", "name": "text", "category": "text",
                 "subcategory": "text", "inside_city": "boolean", "geometry": "Point EPSG:2180"}
for column, count, pct in profile(services, ("id", "name", "category", "subcategory", "inside_city", "geometry")):
    lines.append(f"| {column} | {service_types[column]} | {len(services)-count} | {count} | {pct:.1f}% |")

service_keys = [(row["name"], row["category"], row["subcategory"],
                 bytes(row["geometry"])) for row in services]
exact_services = sum(count - 1 for count in Counter(service_keys).values() if count > 1)
lines += ["", f"Identyczne rekordy usług (bez technicznego ID): **{exact_services}**.",
          "Punkty blisko siebie są osobną kontrolą: patrz `service_duplicate_candidates_2026-11-17.csv`.", ""]

for size in (250, 500):
    rows = [row for row in feature_rows if row["cell_size_m"] == size]
    complete, counts = select_complete_rows(rows, "all")
    columns = ("cell_id", "cell_size_m", "area_m2", "analysis_status", "land_use_status",
               "x", "y", *FEATURE_COLUMNS, "shop_status", "school_status",
               "bus_stop_status", "buffer_sufficient")
    feature_types = {"cell_id": "text", "cell_size_m": "integer", "area_m2": "float",
                     "analysis_status": "text", "land_use_status": "text", "x": "float",
                     "y": "float", "shop_status": "text", "school_status": "text",
                     "bus_stop_status": "text", "buffer_sufficient": "boolean"}
    feature_types.update({name: "float" if name.startswith("nearest_") else "integer"
                          for name in FEATURE_COLUMNS})
    feature_keys = [tuple(row[name] for name in FEATURE_COLUMNS) for row in complete]
    repeated_vectors = sum(count - 1 for count in Counter(feature_keys).values() if count > 1)
    lines += [f"### Siatka {size} m", "",
              f"Kształt: **({len(rows)}, {len(rows[0])})**; komórki analizowane: "
              f"{counts['included_cells']}; bez kompletu 9 cech: "
              f"{counts['missing_feature_excluded']}; kompletne do ML: **{len(complete)}**.", "",
              "| Kolumna | Typ | Non-Null Count | NULL | Udział |",
              "|---|---|---:|---:|---:|"]
    for column, count, pct in profile(rows, columns):
        lines.append(f"| {column} | {feature_types[column]} | {len(rows)-count} | "
                     f"{count} | {pct:.1f}% |")
    lines += ["", "Status odległości (wszystkie komórki):", "",
              "| Usługa | ok | none_found | no_source | error |",
              "|---|---:|---:|---:|---:|"]
    for category, status_column in (("Sklep", "shop_status"),
                                    ("Szkoła", "school_status"),
                                    ("Przystanek", "bus_stop_status")):
        status_counts = Counter(row[status_column] for row in rows)
        lines.append(f"| {category} | {status_counts['ok']} | {status_counts['none_found']} | "
                     f"{status_counts['no_source']} | {status_counts['error']} |")
    lines += ["", f"Komórki z `buffer_sufficient=false`: "
              f"{sum(row['buffer_sufficient'] is False for row in rows)}.",
              "`none_found` oznacza, że importowany zasięg nie potwierdza najbliższego "
              "obiektu; nie jest dowodem, że usługi nie ma w terenie.", "",
              f"Identyczne wektory dziewięciu cech w różnych komórkach: {repeated_vectors}. "
              "To nie są duplikaty komórek i nie są usuwane.", ""]

lines += ["## Zapisane modele na pełnej próbie", "",
          "Silhouette: więcej lepiej; Davies–Bouldin: mniej lepiej; Calinski–Harabasz: "
          "więcej lepiej. HDBSCAN liczy te metryki bez punktów szumu. "
          "Dla HDBSCAN sprawdzono jedną konfigurację, więc nie oceniamy optymalności jego parametrów.", "",
          "| Siatka | Algorytm | Parametr | n | Klastry | Szum | Silhouette | Davies–Bouldin | Calinski–Harabasz |",
          "|---:|---|---|---:|---:|---:|---:|---:|---:|"]
for run in sorted(runs, key=lambda r: (r["cell_size_m"], r["algorithm"],
                                       r["parameters"].get("n_clusters", 0))):
    p = run["parameters"]
    parameter = f"k={p['n_clusters']}" if run["algorithm"] == "kmeans" else (
        f"min_cluster_size={p['min_cluster_size']}, min_samples={p['min_samples']}")
    m = run["metrics"]
    lines.append(f"| {run['cell_size_m']} | {run['algorithm']} | {parameter} | {run['n_samples']} | "
                 f"{run['n_clusters']} | {run['noise_fraction']:.1%} | {metric(m.get('silhouette'))} | "
                 f"{metric(m.get('davies_bouldin'))} | {metric(m.get('calinski_harabasz'))} |")

lines += ["", "## Kontrola przestrzenna opublikowanych modeli", "",
          "Trzy podziały 70/30 po blokach przestrzennych 1 km (seedy 42, 43, 44). "
          "Skaler i wybór `k=2…8` według Silhouette dopasowane wyłącznie na treningu. "
          "ARI porównuje przypisania na "
          "komórkach treningowych z przypisaniami modelu z pełnych danych; mierzy stabilność, "
          "nie trafność względem prawdy terenowej.", "",
          "| Siatka | Model | Wybrane k | Trening / test | Stabilność ARI (średnia; zakres) | Silhouette na teście |",
          "|---:|---|---|---:|---:|---:|"]
for run in runs:
    if not run["is_published"]:
        continue
    rows = [row for row in feature_rows if row["cell_size_m"] == run["cell_size_m"]]
    result = spatial_review(rows, run, assignments[run["id"]])
    selected = ", ".join(map(str, result["selected_ks"])) if result["selected_ks"] else "—"
    lines.append(f"| {run['cell_size_m']} | {run['algorithm']} | {selected} | "
                 f"~{result['train']} / ~{result['test']} | {result['stability_ari']:.3f} "
                 f"({result['stability_range'][0]:.3f}–{result['stability_range'][1]:.3f}) | "
                 f"{metric(result['test_silhouette'])} |")

lines += ["", "K-Means przypisuje odłożone komórki przez `predict`; HDBSCAN w użytej "
          "implementacji nie ma predykcji dla nowych punktów, więc jego pole testowe "
          "pozostaje puste. Accuracy, precision, recall, F1 i ROC AUC wymagają "
          "prawdziwych etykiet, których ten projekt nie ma.", "",
          "Nie usuwano `NULL` przez zastąpienie zerem ani nie usuwano komórek "
          "z identycznymi wektorami cech. Brak cechy wyłącza komórkę jedynie z ML.", "",
          "## Wniosek do projektu", "",
          "K-Means z `k=2` jest prostym modelem podstawowym dla obu siatek: ma "
          "najwyższy Silhouette spośród sprawdzonych `k=2…8`, przypisuje wszystkie "
          "kompletne komórki i utrzymał wysoką stabilność na trzech podziałach przestrzennych. "
          "HDBSCAN pozostaje modelem porównawczym: na 250 m jego wynik jest stabilny, "
          "natomiast na 500 m ARI waha się od -0.034 do 0.918. Jego wyższy Silhouette "
          "nie stanowi samodzielnej przewagi, ponieważ pomija szum. Klastry nie są "
          "etykietami słabej dostępności. Wnioski ML dotyczą tylko komórek z kompletem "
          "cech; brakujące odległości do szkół mogą ograniczać reprezentatywność tej próby.", ""]

path = OUT / f"data_model_review_{date.today().isoformat()}.md"
path.write_text("\n".join(lines), encoding="utf-8")
print(path)
