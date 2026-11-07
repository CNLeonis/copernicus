# Kontrakt API

Wszystkie endpointy mają prefiks `/api` i zwracają JSON. Frontend używa względnych adresów, np. `fetch('/api/health')`; Vite przekazuje je do backendu. Zmiany kontraktu (ścieżka, metoda, dane wejściowe, odpowiedź, błędy) zapisujemy tutaj w tym samym PR co implementację.

**Status:** Zaimplementowano endpointy odczytu metadanych, granicy, usług, siatki, zapisanych przebiegów ML oraz `POST /api/analyze-location`. Żaden endpoint nie trenuje modelu; frontend może nadal korzystać z lokalnego mocka `mode: "demo"` podczas analizy punktu.

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

Parametry: `grid_version`, `algorithm` (`kmeans` | `hdbscan`), `published` (opcjonalny filtr; `true` zwraca wyłącznie runy wybrane do mapy), `limit` (domyślnie 100, maks. 500) i `offset`. Bez `published` zwracane są zarówno runy robocze, jak i opublikowane.

Odpowiedź zawiera `items`, `total`, `limit` i `offset`. Obiekt runu: `id`, `algorithm`, `experiment`, `parameters`, `feature_version`, `grid_version`, `data_version`, `sample_filter`, `feature_set`, `feature_names`, `transform`, `scaler_name`, `random_state`, `n_samples`, `n_clusters`, `noise_fraction`, `metrics`, `is_published`, `created_at`.

`metrics` zawiera `silhouette`, `davies_bouldin`, `calinski_harabasz` (lub `null`, gdy metryka nie ma zastosowania, np. mniej niż 2 klastry) oraz dla HDBSCAN `evaluated_fraction` – odsetek obserwacji użytych do metryk po odrzuceniu szumu.

## GET /api/ml/runs/{run_id}

Ten sam obiekt co w liście oraz `library_versions`, `artifact_path` i `cluster_sizes` (`[{"cluster_label": 0, "cell_count": 0}]`). `404` dla nieznanego runu.

## GET /api/ml/runs/{run_id}/clusters

Parametr opcjonalny: `assessment_version`. Lista grup: `cluster_label`, `is_noise`, `cell_count`, `area_m2`, `sample_share`, `feature_statistics` (dla każdej z 9 cech: `median`, `q1`, `q3`, `min`, `max`, `missing`), a przy podanym `assessment_version` także `low_access_share` (udział ocenionych komórek klastra z klasą `low`; `null`, gdy żadna komórka nie ma jeszcze oceny). `-1` HDBSCAN jest zwracany jako osobna grupa szumu.

## GET /api/ml/runs/{run_id}/assignments

Parametry: `limit` (domyślnie 5000, maks. 10000), `offset`. Odpowiedź: `{"run_id": "...", "total": 0, "limit": 5000, "offset": 0, "items": [{"cell_id": "...", "cluster_label": 0, "is_noise": false}]}`.

## POST /api/analyze-location

Zastępuje wcześniejszy roboczy kontrakt `POST /api/evaluate` (z kategorią `park`, która jest poza zakresem specyfikacji).

Żądanie:

```json
{"latitude": 50.67, "longitude": 17.92, "distance_method": "euclidean", "radius_m": 1000, "grid_version": null}
```

- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180] — wymagane.
- `distance_method`: tylko `euclidean` (odległość sieciowa nie jest w MVP; inna wartość → `422`).
- `radius_m`: 100–2000 (górna granica wynika z bufora pobranych danych 2 km), domyślnie 1000.
- Brak wskazanej siatki lub komórki obejmującej punkt oznacza `404`.
- Jeśli punkt leży na wspólnej krawędzi komórek, wybierana jest komórka o leksykograficznie najmniejszym `cell_id`.
- `grid_version`: opcjonalna wersja siatki; domyślnie najnowsza siatka 250 m. Import usług jest wybierany na podstawie granicy powiązanej z siatką, a wersja cech musi być zgodna z siatką i importem.
- Punkt poza granicą Opola → `422` z komunikatem.

Odpowiedź `200` (liczby poniżej są tylko ilustracją formatu):

```json
{
  "location": {"latitude": 50.67, "longitude": 17.92},
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

`point_features` są obliczane dla dokładnego punktu w EPSG:2180; `cell_features` zawierają zapisane wartości dla przypisanej komórki. Zliczenia dotyczą `radius_m`. Gwarantowany zasięg pokrycia wynika z udokumentowanego bufora importu i odległości punktu od granicy miasta. Jeśli dla kategorii brakuje źródła albo nie można potwierdzić pełnego zasięgu, jej cechy punktu mają wartość `null`, a nie zero. Ostrzeżenia mogą zawierać kody: `no_source:<category>`, `insufficient_buffer:<category>`, `nearest_distance_uncovered:<category>`, `no_feature_version`, `no_cell_features`, `cell_feature_no_source:<category>`, `cell_feature_error:<category>`, `cell_feature_buffer_insufficient`, `no_published_model` i `no_assessment`.
`clustering` zawiera przypisania zapisanej komórki z opublikowanych przebiegów dla wybranej siatki; endpoint nie trenuje modeli ani nie przewiduje wyniku dla nowych obserwacji. Etykieta HDBSCAN `-1` jest zachowywana wraz z `is_noise: true` i nie oznacza niskiej dostępności. `assessment` zawiera zapisane oceny komórki dla wybranej wersji cech. Brak modeli lub ocen oznacza pustą listę i ostrzeżenie. W odpowiedzi API pole `mode` nie występuje; `mode: "demo"` jest dodawane wyłącznie przez mock frontendu.

### Jak rozumieć odpowiedź i prezentować ją w interfejsie

Odpowiedź łączy dwie perspektywy: obliczenia dla dokładnie wskazanego punktu oraz zapisane wyniki komórki siatki, w której leży ten punkt. Nie należy przedstawiać tych wartości jako jednego zestawu pomiarów.

#### Kontekst i pochodzenie danych

- `mode`: w odpowiedzi API to pole nie występuje, bo endpoint zawsze zwraca obliczony wynik. Mock frontendu ustawia `mode: "demo"`, aby interfejs wyraźnie odróżniał przykładowe dane od odpowiedzi API.
- `location`: współrzędne analizowanego punktu w WGS84. Punkt poza granicą miasta dostaje `422`, dlatego udana odpowiedź nie potrzebuje osobnego pola `inside_city`.
- `cell_id` i `grid_version`: wskazują komórkę oraz rozdzielczość siatki, do której przypisano punkt.
- `distance_method` i `radius_m`: opisują sposób i zasięg obliczeń dla `point_features`. Metoda MVP to odległość euklidesowa w EPSG:2180, czyli odległość w linii prostej, nie trasa piesza.
- `data_version` i `feature_version`: pozwalają ustalić, z którego importu OSM i której zapisanej wersji cech pochodzą wyniki. Warto pokazać je w szczegółach albo sekcji informacji o danych, aby wynik dało się odtworzyć.

#### `point_features` — cechy dokładnego punktu

Są obliczane podczas żądania dla współrzędnych podanych przez użytkownika:

- `nearest_shop_m`, `nearest_school_m`, `nearest_bus_stop_m`: odległość w metrach od punktu do najbliższej usługi danej kategorii.
- `shops_within_radius`, `schools_within_radius`, `bus_stops_within_radius`: liczba obiektów danej kategorii w odległości nie większej niż `radius_m`.

To wynik GIS dla klikniętego miejsca, a nie wejście do treningu K-Means/HDBSCAN. Jeśli wartość wynosi `null`, nie ma źródła albo dane nie pozwalają potwierdzić pokrycia; `0` oznacza prawidłowo policzone zero obiektów. Interfejs powinien pokazywać te stany odrębnie.

#### `cell_features` — zapisane cechy komórki

To dziewięć cech zapisanych wcześniej dla komórki `cell_id`: trzy odległości do najbliższych usług oraz sześć zliczeń w promieniu 500 i 1000 m. Są liczone dla punktu reprezentatywnego komórki, a nie dla dokładnego punktu klikniętego przez użytkownika. Dlatego ich wartości mogą się różnić od `point_features`; to oczekiwane.

Wersjonowane cechy komórek są danymi, z których offline pipeline ML przygotowuje eksperymenty. Każdy run zapisuje własne `feature_names` i `feature_version`, więc nie każdy model musi używać wszystkich dziewięciu kolumn. Sam endpoint nie przekazuje punktu do modelu i nie wykonuje treningu ani predykcji dla nowych współrzędnych. `cell_features` są przydatne w UI do pokazania profilu komórki i porównania go z dokładnym punktem.

#### `clustering` — zapisane przypisanie ML

To lista przypisań komórki do opublikowanych runów K-Means/HDBSCAN, nie ocena dostępności dla dokładnego punktu. `cluster_label` jest identyfikatorem grupy, a nie rangą. Dla HDBSCAN `cluster_label: -1` wraz z `is_noise: true` oznacza szum, czyli komórkę o profilu niepasującym do utworzonych skupisk; nie oznacza niskiej dostępności. Pusta lista i `no_published_model` znaczą, że dla tej komórki nie ma opublikowanego przypisania do pokazania.

#### `assessment` — niezależna ocena według reguł

To zapisany wynik jawnych reguł dostępności, odrębny od grupowania ML. `assessment_version` identyfikuje wersję reguł, `variant` mówi, czy ocena jest względna, czy progowa, `accessibility_class` zawiera klasę, a `accessibility_score` i `criteria_results` pozwalają pokazać wynik oraz jego uzasadnienie. Pusta lista i `no_assessment` oznaczają, że ocena nie została jeszcze obliczona/zapisana. Brak oceny nie powinien być zamieniany na klasę niską ani na zero.

#### Wskazówki dla interfejsu

- Rozdzielić sekcję „Wskazany punkt” (`point_features`) od „Komórka siatki” (`cell_id`, `cell_features`, `clustering`, `assessment`).
- Nie łączyć klastra z oceną dostępności: to różne wyniki odpowiadające na różne pytania.
- Wyświetlać `null` jako „Brak danych” lub „Nie można potwierdzić”, a `0` jako zero obiektów.
- Traktować `no_published_model` i `no_assessment` jako informację o braku zapisanych wyników, nie jako błąd obliczeń punktu.
- Oznaczać odpowiedź `mode: "demo"` jako demonstracyjną, żeby nie pomylić jej z obliczeniami dla klikniętego miejsca.

Wyniki API opisują dane OSM i przyjęte obliczenia przestrzenne. Same w sobie nie dowodzą, że mieszkańcy mają lub nie mają dostępu do usługi ani nie stanowią rekomendacji urbanistycznej.
