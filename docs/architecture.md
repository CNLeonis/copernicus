# Architektura

Aplikacja wskazuje obszary Opola o ograniczonej przestrzennej dostępności sklepów spożywczych, szkół i przystanków. Rozdziela trzy poziomy: cechy GIS (odległości i liczby obiektów), grupowanie ML (podobieństwo komórek) i ocenę dostępności według jawnych reguł. Klaster nie jest oceną jakości życia.

## Składniki

```text
OpenStreetMap (Overpass) ──► backend/pipeline (offline, CLI) ──► PostgreSQL + PostGIS ──► FastAPI (backend/app) ──► React + Leaflet (frontend)
                                  │
                                  └─► backend/data (lokalny bufor pobrań i raporty, poza repozytorium)
```

- **Pipeline offline** (`backend/pipeline`, uruchamiany `python -m pipeline.<krok>`): migracje, audyt i import OSM, siatka, cechy, trening modeli, reguły dostępności. Tylko tutaj odpytywany jest Overpass i trenowane są modele.
- **Baza** (PostGIS, schemat w `backend/db/migrations`): geometrie w EPSG:2180 (metry); każdy wynik ma wersję (`data_version`, `grid_version`, `feature_version`, `run_id`, `assessment_version`), więc można odtworzyć, z jakich danych powstał.
- **API** (`backend/app`): wyłącznie odczyt zapisanych wyników oraz obliczenie odległości dla klikniętego punktu w PostGIS. Kliknięcie mapy nie trenuje modelu. Kontrakt: [api.md](api.md).
- **Frontend** (`frontend`): mapa Leaflet z podkładem OSM, analiza wskazanego punktu; dopóki endpoint nie istnieje, używa mocka oznaczonego `mode: "demo"`.
- **Uruchomienie**: `compose.yaml` (serwisy `db`, `backend`, `frontend`).

## Odstępstwa od specyfikacji

Specyfikacja projektu jest dokumentem zespołu przechowywanym lokalnie, poza repozytorium. Poniższe decyzje świadomie od niej odbiegają:

| Specyfikacja | Repozytorium | Powód |
| --- | --- | --- |
| Frontend w React + TypeScript | React w JavaScript (JSX) | Istniejący kod jest w JSX; decyzja zespołu, by go nie przepisywać. Kształt danych opisuje `docs/api.md`. |
| Skrypty offline w głównym katalogu `scripts/`, dane w `data/`, `artifacts/` | `backend/pipeline/`, `backend/data/`, `backend/artifacts/` (oba katalogi ignorowane przez git) | Pipeline współdzieli konfigurację i połączenie z bazą z API oraz mieści się w zakresie roli backendu w `scripts/team-git.mjs`. |
| `POST /api/evaluate` (roboczy kontrakt z kategorią „park”) | `POST /api/analyze-location` z sekcji 6.2 specyfikacji | Parki są poza zakresem; specyfikacja obejmuje tylko sklepy, szkoły i przystanki. |
| Testy pytest/UI w repozytorium i CI | Testy uruchamiane lokalnie (`backend/tests`, `frontend/src/**/*.test.*` poza repozytorium); CI waliduje Compose i buduje obrazy oraz frontend | Decyzja zespołu: testy służą do lokalnej weryfikacji etapów. Wyniki są raportowane w opisach PR. |
| Model danych z sekcji 6.1 | Dodatkowe tabele `city_boundary`, `land_cover`, `grid_versions`, `feature_versions`, `assessment_versions` i kolumny statusów | Potrzebne do `/api/boundary`, trybu „potencjalnie mieszkaniowe”, kontroli punktu reprezentatywnego i wersjonowania wyników. |
