# Copernicus

Aplikacja do projektu z analizy danych przestrzennych. Repozytorium zawiera dwa niezależne serwisy: API w `backend/` (FastAPI) i interfejs w `frontend/` (React + Vite). `compose.yaml` uruchamia je razem w środowisku developerskim.

## Uruchomienie

Wymagany jest uruchomiony Docker Desktop z Compose. W katalogu głównym repozytorium wykonaj:

```sh
docker compose up --build
```

- Interfejs: <http://localhost:5173>
- Dokumentacja API: <http://localhost:8000/docs>
- Kontrola API: <http://localhost:8000/api/health>

Zmiany w `backend/app/` i `frontend/` odświeżają się podczas pracy. Logi wybranego serwisu: `docker compose logs -f backend` lub `docker compose logs -f frontend`. Zatrzymanie: `docker compose down`.

Przy pierwszym starcie Compose pobiera obrazy bazowe oraz instaluje zależności. Jeśli zmieni się `backend/requirements.txt` albo `frontend/package-lock.json`, uruchom ponownie `docker compose up --build`. Po zmianie zależności frontendu może być potrzebne odtworzenie wolumenu `frontend_node_modules` przez `docker compose down -v`; usuwa to tylko lokalne dane wolumenów tego projektu.

## Podział pracy

| Obszar | Główne pliki | Odpowiedzialność |
| --- | --- | --- |
| Backend | `backend/`, `docs/api.md` | Endpointy, dane OSM, obliczenia, model i kontrakt API |
| Frontend | `frontend/` | Mapa, geolokalizacja, widoki i lokalna odpowiedź demonstracyjna |
| Wspólne | `compose.yaml`, `README.md`, `.github/` | Integracja; zmiany uzgadniane w PR |

Frontend wywołuje ścieżki `/api/...`. Vite przekazuje je do backendu wewnątrz sieci Compose, dlatego w kodzie przeglądarki wystarczy `fetch('/api/...')`. Ustalenia dotyczące odpowiedzi API zapisujemy w [docs/api.md](docs/api.md). Backend może dodawać endpointy niezależnie; frontend może równolegle rozwijać widoki na podstawie uzgodnionego kontraktu.

Analiza punktu korzysta z kontraktu `POST /api/analyze-location` (zastąpił
roboczy `POST /api/evaluate`). Dopóki endpoint nie jest zaimplementowany,
frontend używa mocka oznaczonego `mode: "demo"`. Dalsze zmiany kontraktu
wymagają uzgodnienia przed użyciem nowych pól przez frontend. Architekturę i
świadome odstępstwa od specyfikacji opisuje [docs/architecture.md](docs/architecture.md).

## Baza danych i testy lokalne

`docker compose up -d db` uruchamia PostGIS. Wartości domyślne są w
[.env.example](.env.example).

### Odtworzenie danych od zera

Obie osoby odtwarzają identyczną bazę tą samą sekwencją (wystarczy Docker,
lokalne środowisko Pythona nie jest potrzebne):

```sh
docker compose up -d db
docker compose run --rm backend python -m pipeline.migrate
docker compose run --rm backend python -m pipeline.import_osm
docker compose run --rm backend python -m pipeline.grid
```

- `pipeline.migrate` tworzy schemat; ponowne uruchomienie pomija zastosowane migracje.
- `pipeline.import_osm` pobiera z Overpass stan OSM przypięty do stałej daty
  (`[date:"..."]`), więc wynik nie zależy od dnia pobrania. Odpowiedzi trafiają do
  `backend/data/raw/` i są używane ponownie. Ponowny import tej samej wersji jest
  pomijany; `--replace` odtwarza ją, usuwając wyniki od niej zależne. Na końcu
  polecenie wypisuje liczności i sumy kontrolne – porównaj je z
  [docs/data_sources.md](docs/data_sources.md#import--27102026).
- `pipeline.grid` buduje siatki 250 m i 500 m dla najnowszego importu (kilkanaście
  sekund); istniejąca wersja siatki jest pomijana, `--replace` ją odtwarza.
- Pobieranie trwa kilka minut. Publiczny Overpass bywa przeciążony (HTTP 502/504);
  klient sam ponawia wtedy zapytanie.

Testy są z decyzji zespołu trzymane lokalnie (`backend/tests/`,
`frontend/src/**/*.test.*`) i nie trafiają do repozytorium ani do CI; wyniki
podajemy w opisie PR. Backend: w `backend/` zainstaluj
`requirements-dev.txt` i uruchom `python -m pytest` (testy bazy używają
`TEST_DATABASE_URL`, domyślnie bazy `copernicus_test` w kontenerze `db`).
Frontend: `npm --prefix frontend test`.

## Gałęzie etapów (`stage/*`)

Realizacja planu projektu idzie etapami. Każde zadanie etapu to osobna, mała
gałąź `stage/<etap>-<temat>` (np. `stage/2-audyt-osm`) i osobny PR do `main`
z jednym tematem. Gdy zadanie zależy od nieskalonego jeszcze PR, gałąź
tworzymy z gałęzi tego PR i ustawiamy ją jako base PR (stacked PR); po
scaleniu zależności bazą jest `origin/main`. Opis PR podaje: co zrobiono,
uruchomione testy i wynik, ograniczenia i zależności. Gałęzie `stage/*`
obejmują pliki wielu ról, dlatego nie używamy dla nich `scripts/team-git.mjs`,
tylko zwykłych poleceń `git` (`git switch -c`, `git add <pliki>`, `git push -u`).
Przed commitem sprawdzamy `git diff --cached --name-only`.

## Branche i commity

Po wspólnym commicie startowym każda osoba pracuje w osobnym klonie repozytorium albo osobnym `git worktree` (samo przełączanie branchy w jednym katalogu nie daje równoległej pracy). Gałęzie to `feature/frontend-map` i `feature/backend-evaluation`. Nie commitujemy bezpośrednio do `main`.

Przykład dla backendu; osoba od frontendu używa gałęzi `feature/frontend-map` i zmienia tylko `frontend/`:

```sh
git switch main
git pull --ff-only origin main
git switch -c feature/backend-evaluation
# edycja plików w backend/ i, jeśli trzeba, docs/api.md
git add backend docs/api.md
git commit -m "feat(backend): add API endpoint"
git push -u origin feature/backend-evaluation
```

Następnie otwórz pull request do `main`. Przed scaleniem sprawdź wynik GitHub Actions i aktualizuj branch o zmiany z `main`. Zmiany w plikach wspólnych opisuj w PR, aby druga osoba mogła dostosować swoją część. Małe commity związane z jednym zadaniem ułatwiają przegląd i rozwiązywanie konfliktów.

### Skrót do codziennej pracy

W katalogu głównym repozytorium uruchom skrypt przez `node` (nie wymaga modułów npm):

| Rola | Pobierz najnowszy `main` na własną gałąź | Zapisz i wyślij własne zmiany |
| --- | --- | --- |
| Backend | `node scripts/team-git.mjs sync backend` | `node scripts/team-git.mjs publish backend` |
| Frontend | `node scripts/team-git.mjs sync frontend` | `node scripts/team-git.mjs publish frontend` |

`publish` pyta o wiadomość commita; można ją też podać przez `-m "opis"`.
Backend może dołączyć zmianę `docs/api.md` po uzgodnieniu kontraktu, dodając
`--include-api-doc`. Skrypt działa tylko na gałęzi przypisanej do roli, blokuje
commit przy zmianach poza jej katalogiem i nigdy nie wypycha zmian do `main`.
Jeśli nie ma nowych zmian, `publish` po prostu wysyła istniejące commity.

`sync` wymaga czystego katalogu roboczego. Pobiera aktualny stan z GitHub,
scala `origin/main` z lokalną gałęzią i niczego nie wypycha. Przy konflikcie
zatrzymuje się, aby można go było rozwiązać ręcznie; po rozwiązaniu uruchom
`publish`. Skrypt nie tworzy gałęzi i nie zastępuje przeglądu PR.

## Praca bez Compose

Backend: w `backend/` zainstaluj `requirements.txt` w środowisku wirtualnym i uruchom `uvicorn app.main:app --reload`. Frontend: w `frontend/` wykonaj `npm ci`, potem `npm run dev`. Gdy Vite działa poza Dockerem, domyślnie kieruje `/api` do `http://localhost:8000`.
