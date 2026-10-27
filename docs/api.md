# Kontrakt API

Wszystkie endpointy mają prefiks `/api` i zwracają JSON. Frontend używa względnych adresów, np. `fetch('/api/health')`; Vite przekazuje je do backendu. Zmiany kontraktu (ścieżka, metoda, dane wejściowe, odpowiedź, błędy) zapisujemy tutaj w tym samym PR co implementację.

**Status:** kontrakt do uzgodnienia przez zespół. Zaimplementowany jest tylko `GET /api/health`; pozostałe endpointy powstaną w kolejnych etapach. Frontend do tego czasu używa lokalnego mocka oznaczonego `mode: "demo"`.

Kontrakt realizuje sekcję 6.2 `docs/SPECYFIKACJA_PROJEKTU.md`. API tylko **odczytuje** wyniki zapisane przez pipeline offline (`backend/pipeline`); żaden endpoint nie pobiera danych z Overpass ani nie trenuje modeli.

## Zasady wspólne

- **Współrzędne:** wejście i wyjście w WGS84 (EPSG:4326), kolejność GeoJSON `[longitude, latitude]`. Odległości i powierzchnie liczone są w EPSG:2180 i podawane w metrach / m².
- **Wersje:** odpowiedzi z danymi zawierają identyfikatory wersji: `data_version` (import OSM), `grid_version` (np. `g250_v1`), `feature_version`, `run_id` (model), `assessment_version` (reguły dostępności). Gdy parametr wersji jest pominięty, API wybiera najnowszą zapisaną wersję i zwraca jej identyfikator.
- **`null` i braki:** `null` oznacza brak wartości (nie obliczono / nie znaleziono), nigdy zero. Liczba `0` w polach zliczeń to prawidłowa obserwacja. Klasa oceny `no_data` jest odrębna od `low`.
- **Klastry:** `cluster_label` to identyfikator grupy, nie ranga jakości. `-1` oznacza szum HDBSCAN (`is_noise: true`), a nie niedostatek usług.
- **bbox:** `bbox=minLon,minLat,maxLon,maxLat` (WGS84). Niepoprawny format, odwrócone granice lub wartości spoza zakresu → `422`.
- **Stronicowanie:** `limit` i `offset`; odpowiedzi listowe zwracają `total`, `limit`, `offset`. Maksymalny `limit` podano przy endpointach.
- **Błędy:** `422` (walidacja, format FastAPI `{"detail": ...}`), `404` (brak zasobu lub brak zapisanych danych danej wersji, `{"detail": "..."}`), `503` (baza niedostępna). Frontend traktuje każdą odpowiedź spoza `2xx` jako błąd i nie pokazuje wtedy wyniku.

## GET /api/health

```json
{"status": "ok", "database": "ok"}
```

`database` przyjmuje `ok` albo `unavailable`; endpoint zwraca `200`, nawet gdy baza nie odpowiada.

## GET /api/metadata

Źródła, wersje i daty potrzebne w widoku „O projekcie”.

```json
{
  "attribution": "© OpenStreetMap contributors, ODbL 1.0",
  "attribution_url": "https://www.openstreetmap.org/copyright",
  "imports": [{"data_version": "...", "source_name": "...", "source_url": "...", "downloaded_at": "ISO 8601",
               "license_info": "...", "record_count": 0, "import_status": "completed", "validation_report": {}}],
  "grid_versions": [{"grid_version": "g250_v1", "cell_size_m": 250, "crs": "EPSG:2180", "cell_count": 0,
                     "created_at": "ISO 8601", "parameters": {}}],
  "feature_versions": [{"feature_version": "...", "grid_version": "...", "data_version": "...",
                        "distance_method": "euclidean", "parameters": {}, "computed_at": "ISO 8601"}],
  "assessment_versions": [{"assessment_version": "...", "feature_version": "...", "variant": "relative",
                           "description": "...", "parameters": {}}]
}
```

Puste listy oznaczają, że pipeline nie zapisał jeszcze danego typu wyników.

## GET /api/boundary

GeoJSON `Feature` z granicą administracyjną Opola (MultiPolygon). `properties`: `name`, `source_type`, `source_id`, `data_version`. `404`, gdy granica nie została zaimportowana.

## GET /api/services

Parametry: `category` (`shop` | `school` | `bus_stop`, można powtórzyć; domyślnie wszystkie), `bbox` (opcjonalny), `limit` (domyślnie 1000, maks. 5000), `offset`.

Odpowiedź: GeoJSON `FeatureCollection` z punktami oraz `total`, `limit`, `offset`, `data_version`. `properties` obiektu:

```json
{"id": 1, "category": "shop", "subcategory": "supermarket", "name": "...", "source_type": "node",
 "source_id": 123, "source_url": "https://www.openstreetmap.org/node/123", "inside_city": true,
 "tags": {"shop": "supermarket"}}
```

`name` może być `null`, jeżeli obiekt nie ma nazwy w OSM. Usługi spoza granicy (bufor) mają `inside_city: false`.

## GET /api/cells

Parametry: `grid_version` (domyślnie najnowsza siatka 250 m), `bbox`, `feature_version`, `run_id`, `assessment_version`, `limit` (domyślnie 5000, maks. 10000), `offset`.

Odpowiedź: GeoJSON `FeatureCollection` przyciętych do miasta komórek oraz `total`, `limit`, `offset`, `grid_version`, `feature_version`, `run_id`, `assessment_version`. `properties`:

```json
{"id": "g250_v1_c012_r034", "area_m2": 62500.0, "land_use_status": "potentially_residential",
 "analysis_status": "included", "features": {"nearest_shop_m": 0.0, "shops_500m": 0},
 "cluster_label": null, "is_noise": null, "accessibility_class": null, "accessibility_score": null}
```

`features` zawiera 9 cech (`null`, gdy brak wersji cech). `cluster_label` i `is_noise` są wypełnione tylko przy podanym `run_id`, a `accessibility_*` tylko przy podanym `assessment_version`. `land_use_status`: `potentially_residential` | `no_residential_evidence` (brak dowodu zabudowy w OSM, nie „teren pusty”).

## GET /api/cells/{cell_id}

Szczegóły jednej komórki: geometria (Feature), `representative_point` (`[lon, lat]`), metoda punktu reprezentatywnego, wynik kontroli punktu (`ok` | `water` | `forest`), `area_m2`, `building_count`, `land_use_status`, cechy wybranej wersji ze statusami kategorii (`ok` | `none_found` | `no_source` | `error`) i `buffer_sufficient`, lista przypisań do opublikowanych modeli (`run_id`, `algorithm`, `cluster_label`, `is_noise`) oraz lista ocen (`assessment_version`, `variant`, `accessibility_class`, `accessibility_score`, `criteria_results`). `404` dla nieznanego ID.

## GET /api/ml/runs

Parametry: `grid_version`, `algorithm` (`kmeans` | `hdbscan`), `published` (`true` → tylko modele wybrane do mapy).

Lista runów: `id`, `algorithm`, `experiment`, `parameters`, `feature_version`, `grid_version`, `data_version`, `sample_filter`, `feature_set`, `feature_names`, `transform`, `scaler_name`, `random_state`, `n_samples`, `n_clusters`, `noise_fraction`, `metrics`, `is_published`, `created_at`.

`metrics` zawiera `silhouette`, `davies_bouldin`, `calinski_harabasz` (lub `null`, gdy metryka nie ma zastosowania, np. mniej niż 2 klastry) oraz dla HDBSCAN `evaluated_fraction` – odsetek obserwacji użytych do metryk po odrzuceniu szumu.

## GET /api/ml/runs/{run_id}

Ten sam obiekt co w liście oraz `library_versions` i `cluster_sizes` (`[{"cluster_label": 0, "cell_count": 0}]`). `404` dla nieznanego runu.

## GET /api/ml/runs/{run_id}/clusters

Parametr opcjonalny: `assessment_version`. Lista grup: `cluster_label`, `is_noise`, `cell_count`, `area_m2`, `sample_share`, `feature_statistics` (dla każdej z 9 cech: `median`, `q1`, `q3`, `min`, `max`, `missing`), a przy podanym `assessment_version` także `low_access_share` (udział komórek ocenionych jako `low` według niezależnych reguł).

## GET /api/ml/runs/{run_id}/assignments

Parametry: `limit` (domyślnie 5000, maks. 10000), `offset`. Odpowiedź: `{"run_id": "...", "total": 0, "limit": 5000, "offset": 0, "items": [{"cell_id": "...", "cluster_label": 0}]}`.

## POST /api/analyze-location

Zastępuje wcześniejszy roboczy kontrakt `POST /api/evaluate` (z kategorią `park`, która jest poza zakresem specyfikacji).

Żądanie:

```json
{"latitude": 50.67, "longitude": 17.92, "distance_method": "euclidean", "radius_m": 1000, "grid_version": null}
```

- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180] — wymagane.
- `distance_method`: tylko `euclidean` (odległość sieciowa nie jest w MVP; inna wartość → `422`).
- `radius_m`: 100–2000 (górna granica wynika z bufora pobranych danych 2 km), domyślnie 1000.
- Punkt poza granicą Opola → `422` z komunikatem.

Odpowiedź `200` (liczby poniżej są tylko ilustracją formatu):

```json
{
  "mode": "computed",
  "location": {"latitude": 50.67, "longitude": 17.92},
  "inside_city": true,
  "cell_id": "g250_v1_c012_r034",
  "grid_version": "g250_v1",
  "distance_method": "euclidean",
  "radius_m": 1000,
  "point_features": {
    "nearest_shop_m": 220.0, "nearest_school_m": 720.0, "nearest_bus_stop_m": 310.0,
    "shops_within_radius": 5, "schools_within_radius": 1, "bus_stops_within_radius": 4
  },
  "cell_features": {"nearest_shop_m": 240.0, "shops_500m": 2, "...": "9 cech zapisanej komórki"},
  "clustering": [{"run_id": "...", "algorithm": "kmeans", "cluster_label": 2, "is_noise": false}],
  "assessment": [{"assessment_version": "...", "variant": "relative", "accessibility_class": "medium",
                  "accessibility_score": 0.41, "criteria_results": {}}],
  "data_version": "...",
  "feature_version": "...",
  "warnings": []
}
```

`point_features` są liczone dla dokładnego punktu (PostGIS, EPSG:2180); `cell_features` to zapisane cechy komórki, do której należy punkt. `clustering` zawiera przypisanie **zapisanej** komórki w opublikowanych modelach (bez trenowania i bez predykcji dla nowych obserwacji). `warnings` opisuje np. brak opublikowanego modelu, brak ocen albo niewystarczający bufor. `mode` zachowuje konwencję z widoku demonstracyjnego: `demo` oznacza przykładowy mock frontendu, `computed` – wynik backendu.
