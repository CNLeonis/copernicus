# Źródła danych

## OpenStreetMap

- **Licencja:** © OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright). Dane i wyniki pochodne wymagają przypisania autorstwa.
- **Pobieranie:** wyłącznie offline, przez Overpass API (`https://overpass-api.de/api/interpreter`, adres konfigurowalny zmienną `OVERPASS_URL`). Zapytania są wykonywane sekwencyjnie z przerwą, identyfikującym User-Agentem i ponawianiem przy 429/502/503/504. Odpowiedzi trafiają do lokalnego bufora `backend/data/raw/` (poza repozytorium) razem z treścią zapytania i datą pobrania; kolejne uruchomienia czytają bufor (`--refresh` wymusza ponowne pobranie).
- **Brak obiektu w OSM nie dowodzi jego faktycznego braku.**

## Audyt tagów – 27.10.2026

Narzędzie: `python -m pipeline.osm_audit --relation-id 2730708`. Pełny raport (JSON i Markdown) powstaje w `backend/data/reports/osm_audit_2026-10-27.*` i nie jest commitowany.

- Stan danych OSM (`timestamp_osm_base`): **2026-10-27T13:49:01Z** (usługi), 2026-10-27T13:45:02Z (granica); pobrano 2026-10-27 13:50 UTC.
- Obszar audytu: granica Opola + **bufor 2000 m** (EPSG:2180). Zapytanie po bbox bufora `50.56964,17.75157,50.78796,18.06097`; 178 obiektów z narożników bbox spoza bufora odrzucono, w analizie pozostało 1639.
- „Miasto” = wewnątrz granicy, „bufor” = pierścień 0–2 km poza granicą (obiekty potrzebne, by nie zaniżać dostępności przy granicy).

### Granica

| Relacja | admin_level | TERYT | Powierzchnia | Różnica symetryczna względem 2730708 |
| --- | --- | --- | --- | --- |
| 2661906 (powiat) | 6 | 1661 | 148,808 km² | 0 m² |
| **2730708 (gmina miejska)** | 7 | 1661011 | 148,808 km² | – |
| 3172807 (miasto) | 8 | – | 148,808 km² | 0 m² |

Wszystkie trzy relacje mają identyczną geometrię (jeden poligon). Audyt użył relacji 2730708.

### Sklepy (`shop=*`)

Łącznie 582 obiekty w mieście i 89 w buforze (517 węzłów, 153 linie, 1 relacja), w 89 różnych wartościach tagu (86 w samym mieście). Wartości istotne dla wyboru „sklepów spożywczych” (miasto / bufor):

| `shop=` | Miasto | Bufor | Uwagi |
| --- | --- | --- | --- |
| `convenience` | 69 | 9 | 6 bez nazwy |
| `supermarket` | 62 | 7 | |
| `grocery` | 1 | 0 | prawie nieużywany w Opolu |
| `bakery` | 20 | 2 | |
| `pastry` | 10 | 0 | |
| `butcher` | 6 | 1 | |
| `greengrocer` | 5 | 0 | |
| `alcohol` | 4 | 0 | |
| `health_food` | 2 | 0 | |
| `farm` | 2 | 0 | |
| `department_store` | 2 | 0 | |
| `variety_store` | 8 | 3 | m.in. sieci z towarami niespożywczymi (Action, TEDi, Woolworth) |
| `wholesale` | 9 | 1 | hurtownie (np. Eurocash Cash&Carry, hurtownie instalacyjne) |
| `kiosk` | 6 | 1 | kioski prasowe |
| `yes` | 13 | 1 | m.in. stacje paliw – wartość niejednoznaczna |

### Szkoły (`amenity=school`)

- 45 obiektów w mieście, 13 w buforze (17 węzłów, 39 linii, 2 relacje).
- `isced:level` brak w 43 z 45 obiektów w mieście; tag `school=*` nie występuje; `grades` ma 7 obiektów.
- Klasyfikacja po nazwie (miasto; obiekt może trafić do kilku klas): „szkoła podstawowa” 19, liceum 8, zespół szkół 8, inna nazwa 5, brak nazwy 3, szkoła muzyczna/artystyczna 2. Bufor: szkoła podstawowa 8, zespół szkół 2, liceum 1, technikum 1, szkoła branżowa 1.
- Zgodność nazwy z `isced:level` (miasto + bufor): nazwa „podstawowa” i `isced:level` z 1 – 6; nazwa „podstawowa” bez `isced:level` – 20; `isced:level` z 1 bez takiej nazwy – 1.
- 17 par szkół w odległości ≤150 m. Wśród nich: ta sama szkoła jako węzeł i obrys (np. Publiczna Szkoła Podstawowa w Dąbrowie, 67,6 m), obrys bez nazwy obok nazwanego węzła/obrysu, linia i relacja tego samego liceum oraz szkoły językowe oznaczone `amenity=school` (np. „Lucky Language”, „newCOSMOPOLITAN Centrum Języków Obcych”).

### Przystanki

| Kombinacja tagów | Miasto | Bufor |
| --- | --- | --- |
| `highway=bus_stop` + `public_transport=platform` | 483 | 87 |
| `public_transport=stop_position` | 141 | 30 |
| `public_transport=platform` (bez `highway=bus_stop`) | 48 | 16 |
| `public_transport=stop_area` | 35 | 8 |
| `highway=bus_stop` (bez `public_transport`) | 33 | 4 |
| `highway=bus_stop` + `public_transport=stop_position` | 7 | 1 |
| stacje/przystanki kolejowe (`public_transport=station`, `railway=station/halt`) | 12 | 5 |

- Obiekty „platformowe” (`highway=bus_stop` lub `public_transport=platform`): 571 w mieście, 108 w buforze; z tego perony kolejowe: 18 w mieście, 9 w buforze.
- W mieście 553 platformy niekolejowe: 408 ma w nazwie kod stanowiska MZK w nawiasie (np. „Wrocławska - Kościół (511)” i „(512)” dla dwóch kierunków), 45 nie ma nazwy, a po usunięciu kodu jest 275 różnych nazw przystanków.
- 45 relacji `stop_area` w bbox, ale tylko 81 obiektów platformowych jest ich członkami – relacje nie pokrywają całej sieci.
- 63 pozycje zatrzymania (`stop_position`) mają w odległości ≤30 m platformę o tej samej nazwie (alternatywna reprezentacja tego samego miejsca).
- Operator platform: `MZK Opole` 477, `Miejski Zakład Komunikacyjny w Opolu` 4, brak tagu 126, Sindbad 36, PKP PLK 27, PKS Opole 4, `GTV Bus` 2, `MZK Opole, GTV Bus` 1.

### Zabudowa i pokrycie terenu (wewnątrz granicy)

| Warstwa | Obiekty |
| --- | --- |
| `landuse=residential` | 1287 (1269 linii, 18 relacji) |
| `building=*` | 36 372 |
| `natural=water` / `waterway=riverbank` | 238 |
| `landuse=forest` / `natural=wood` | 743 |

## Decyzje zespołu – 27.10.2026

Podjęte na bramce Etapu 2 na podstawie audytu powyżej. Implementacja: `backend/pipeline/import_osm.py`.

| Temat | Decyzja | Uzasadnienie z audytu |
| --- | --- | --- |
| Sklepy | `shop` ∈ {`supermarket`, `convenience`, `grocery`, `greengrocer`, `bakery`, `butcher`, `pastry`}; `subcategory` = wartość `shop`. Pozostałe wartości (m.in. `variety_store`, `wholesale`, `kiosk`, `yes`, `department_store`) są pomijane. | Wykluczone wartości obejmują sieci niespożywcze, hurtownie, kioski prasowe i stacje paliw; `grocery` jest prawie nieużywany, więc sam nie wystarczy. |
| Szkoły | `amenity=school`. `subcategory='primary'`, gdy `isced:level` zawiera 1 albo nazwa pasuje do „Szkoła Podstawowa” / „SP nr”; w przeciwnym razie `'general'`. | `isced:level` brakuje w 43 z 45 szkół w mieście, więc sama etykieta ISCED nie wystarczy. |
| Szkoły językowe | Wykluczone jawną regułą: nazwa zawiera słowo „language”, „języków obcych” lub „szkoła językowa” (wyrażenie `LANGUAGE_SCHOOL_NAME`). Każde wykluczenie jest zapisywane w `validation_report.services.excluded`. | W OSM są oznaczone `amenity=school`, choć nie są szkołami w rozumieniu analizy. |
| Duplikaty szkół | Scalane są tylko różne reprezentacje tego samego obiektu: węzeł leżący w obrysie (linia/relacja) **i** o tej samej znormalizowanej nazwie (`same_name_point_in_outline`) albo obrys bez nazwy zawierający dokładnie jeden nazwany węzeł (`single_named_point_in_unnamed_outline`). Zostaje węzeł, obrys jest pomijany. Sama bliskość lub podobna nazwa nie wystarcza. Każde scalenie i każda pozostała para ≤150 m są zapisywane w `validation_report`. | 17 par ≤150 m obejmuje zarówno duplikaty, jak i różne szkoły w jednym budynku lub zespole. |
| Przystanki | Jednostka = stanowisko: `highway=bus_stop` lub `public_transport=platform`; `stop_position` jest pomijane. Obiekt z oboma tagami jest liczony raz (jeden element OSM = jeden rekord). Kategoria w bazie pozostaje `bus_stop`; `subcategory='rail_platform'` dla peronów kolejowych (`railway=platform` lub `train=yes`), w pozostałych przypadkach `'bus_tram_stop'`. Cechy obejmują obie podkategorie. | 63 pozycje `stop_position` dublują platformy o tej samej nazwie. Perony kolejowe stanowią **rozszerzenie specyfikacji**, która wymienia przystanki komunikacji publicznej bez rozróżnienia. |
| Granica | Relacja **2730708** (gmina miejska Opole, TERYT 1661011). | Trzy relacje mają identyczną geometrię; wybrano jednostkę gminną. |
| Snapshot danych | Danych OSM nie commitujemy. W repozytorium jest kod pobierania, a tutaj stan `osm_base`, zapytania i liczności. | Licencja ODbL i rozmiar; odtwarzalność zapewnia przypięcie daty (niżej). |

## Import – 27.10.2026

Polecenie: `docker compose run --rm backend python -m pipeline.import_osm` (pełna sekwencja w [README](../README.md#odtworzenie-danych-od-zera)).

### Odtwarzalność

- Wszystkie zapytania mają nagłówek Overpass `[date:"2026-10-27T13:49:01Z"]` (tzw. zapytanie archiwalne, *attic*). Zwracają stan OSM z chwili `osm_base` audytu niezależnie od dnia pobrania. Działanie sprawdzono przed importem: w tym samym bbox `shop=supermarket` dało 82 obiekty dla tej daty, 79 dla 2025-01-01 i 82 dla stanu bieżącego.
- `data_imports.data_version = osm-2026-10-27T13:49:01Z`; ponowny import tej wersji jest pomijany (`--replace` go odtwarza).
- Zapytania archiwalne nie korzystają z `area()`. Warstwy pokrycia terenu są pobierane w bbox granicy i filtrowane w Pythonie przecięciem z poligonem granicy, a usługi – w bbox bufora i filtrowane odległością ≤2000 m.
- Do porównania baz obu osób służą sumy kontrolne SHA-256 posortowanych identyfikatorów (`validation_report`). Ta sama wersja danych powinna dać te same sumy.
- Pełny raport: `data_imports.validation_report` oraz `backend/data/reports/import_osm_report.json` (zawiera treść wszystkich zapytań).

### Usługi (`services`)

| Kategoria | Podkategoria | Miasto | Bufor 0–2 km |
| --- | --- | --- | --- |
| `shop` | `convenience` | 69 | 9 |
| `shop` | `supermarket` | 62 | 7 |
| `shop` | `bakery` | 20 | 2 |
| `shop` | `pastry` | 10 | 0 |
| `shop` | `butcher` | 6 | 1 |
| `shop` | `greengrocer` | 5 | 0 |
| `shop` | `grocery` | 1 | 0 |
| `school` | `primary` | 19 | 8 |
| `school` | `general` | 23 | 4 |
| `bus_stop` | `bus_tram_stop` | 553 | 99 |
| `bus_stop` | `rail_platform` | 18 | 9 |

- Suma kontrolna usług: `180ba8340867fb3b1fde1c2f0f2584d8e7d954ef247db944965b361416fcff5a`.
- Odrzucone poza buforem (narożniki bbox): sklepy 27, przystanki 95, szkoły 12. Bez nazwy: sklepy 14, przystanki 78, szkoły 2.
- Wykluczone szkoły językowe (2): „Lucky Language” (node/3774935038), „newCOSMOPOLITAN Centrum Języków Obcych” (node/3826146441).
- Scalenia szkół (2): „Publiczna Szkoła Podstawowa w Dąbrowie” – node/11581824414 w obrysie way/704539922 o tej samej nazwie; „Publiczna Szkoła Podstawowa nr 24” – node/859299561 jako jedyny nazwany węzeł w nienazwanym obrysie way/319029743.
- Po scaleniu zostaje 11 par szkół ≤150 m, których reguła nie łączy. Są to różne szkoły w jednym miejscu (np. PSP nr 3 i liceum, 8 m) albo przypadki niejednoznaczne: dwie reprezentacje „PLO nr VI”/„PLO nr 6” (way/227969035 i relation/2783107, 8,9 m, różnie zapisane nazwy) oraz nienazwany obrys way/232369822 obok „Niepublicznej SP Nasza Szkoła”. Lista jest w raporcie.
- Uwaga: „Publiczne Przedszkole nr 36” i „Instytut Szkolenia Ekonomiczno-Prawnego” mają w OSM `amenity=school`. Decyzje ich nie wykluczają, więc są liczone jako `general`.

### Pokrycie terenu (`land_cover`, wewnątrz granicy)

| Rodzaj | Pobrane (bbox) | Poza granicą | Zapisane | Suma kontrolna SHA-256 |
| --- | --- | --- | --- | --- |
| `residential` | 1842 | 554 | 1288 | `14671b8983bb13574c4126abd6ed629a8880b66bb2706248654f7d23288eb2fe` |
| `building` | 56 576 | 20 211 | 36 365 | `82a505ca2b9d06df9382164da38fff9fc22d782680e6df496ce6f53c754d912e` |
| `water` | 420 | 182 | 238 | `ad8ef3f42378fd169c41a85d969a0cb7656a98fd3e58491cb6bf4c7f63e5e944` |
| `forest` | 1290 | 546 | 744 | `630ca136063dd4f27e88e98889489b0161bc550f39640d3dae3ab605223f8f16` |

Zapisywane są wyłącznie poligony (linie zamknięte i multipoligony) przecinające granicę; niepoprawne geometrie są naprawiane przez `make_valid`. Drobne różnice względem audytu (np. budynki 36 365 zamiast 36 372) wynikają z innej metody: audyt liczył filtrem obszaru Overpass, który obejmuje też węzły, a import liczy poligony przecinające geometrię granicy.
