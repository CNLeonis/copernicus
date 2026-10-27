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

## Otwarte decyzje przed importem (Etap 2B)

Zespół wybiera na podstawie powyższych liczb: listę wartości `shop=*` uznanych za sklepy spożywcze, regułę rozpoznania szkół podstawowych, jednostkę liczenia przystanków i obsługę duplikatów, relację granicy oraz to, czy commitować snapshot danych. Do czasu decyzji import nie jest implementowany.
