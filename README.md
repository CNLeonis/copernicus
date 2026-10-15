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
| Backend | `backend/` | Endpointy, logika, zależności Pythona |
| Frontend | `frontend/` | Widoki, komponenty, zależności npm |
| Wspólne | `compose.yaml`, `docs/api.md`, `README.md`, `.github/` | Integracja i kontrakt API; zmiany uzgadniane w PR |

Frontend wywołuje ścieżki `/api/...`. Vite przekazuje je do backendu wewnątrz sieci Compose, dlatego w kodzie przeglądarki wystarczy `fetch('/api/...')`. Ustalenia dotyczące odpowiedzi API zapisujemy w [docs/api.md](docs/api.md). Backend może dodawać endpointy niezależnie; frontend może równolegle rozwijać widoki na podstawie uzgodnionego kontraktu.

## Branche i commity

Każda osoba pracuje w osobnym klonie repozytorium albo osobnym `git worktree` (samo przełączanie branchy w jednym katalogu nie daje równoległej pracy). Nie commitujemy bezpośrednio do `main`.

Przykład dla backendu; osoba od frontendu używa analogicznie nazwy `feat/frontend-...`:

```sh
git switch main
git pull --ff-only origin main
git switch -c feat/backend-api
# edycja plików w backend/ i, jeśli trzeba, docs/api.md
git add backend docs/api.md
git commit -m "feat(backend): add API endpoint"
git push -u origin feat/backend-api
```

Następnie otwórz pull request do `main`. Przed scaleniem sprawdź wynik GitHub Actions i aktualizuj branch o zmiany z `main`. Zmiany w plikach wspólnych opisuj w PR, aby druga osoba mogła dostosować swoją część. Małe commity związane z jednym zadaniem ułatwiają przegląd i rozwiązywanie konfliktów.

## Praca bez Compose

Backend: w `backend/` zainstaluj `requirements.txt` w środowisku wirtualnym i uruchom `uvicorn app.main:app --reload`. Frontend: w `frontend/` wykonaj `npm ci`, potem `npm run dev`. Gdy Vite działa poza Dockerem, domyślnie kieruje `/api` do `http://localhost:8000`.
