# Frontend Copernicus

Interfejs React + Vite, mapa Leaflet ustawiona początkowo na Opole.

```sh
npm ci
npm run dev
npm run build
npm test
```

`npm test` uruchamia Vitest (jsdom, Testing Library). Pliki `*.test.js(x)` są
z decyzji zespołu trzymane lokalnie i nie trafiają do repozytorium; bez nich
polecenie kończy się sukcesem bez uruchomionych testów.

Kliknij mapę, aby wybrać punkt, lub użyj geolokalizacji. Dla obsługi klawiaturą
ustaw fokus na mapie, przesuń ją strzałkami i naciśnij „Wybierz środek mapy”.
„Wróć do Opola” przywraca widok miasta bez zmiany wybranego punktu.
Geolokalizacja wymaga localhost lub HTTPS; odmowa nie blokuje wyboru na mapie.
Podkład mapy pochodzi z OpenStreetMap i wymaga połączenia z internetem.

## Dane i późniejsze podłączenie API

`src/services/analysis.js` udostępnia `analyzeLocation({ latitude, longitude },
{ signal, radiusM })` zgodnie z kontraktem `POST /api/analyze-location` w
`../docs/api.md`. Dopóki endpoint nie jest zaimplementowany, domyślnie zwraca
kopię `src/mocks/analyze-location.json` (`mode: "demo"`, wszystkie wartości
`null`). Cały wynik demo ma wspólne oznaczenie i komunikat, że nie dotyczy
wybranego punktu. `null` wyświetla „Brak danych”, a liczba `0` jest pokazywana
jako prawidłowa wartość. Zmienna `VITE_ANALYSIS_SOURCE=api` przełącza serwis na
prawdziwe API; odpowiedź spoza `2xx` jest błędem (z treścią `detail`, jeśli
backend ją zwrócił). Komponenty widoku nie pobierają danych.
Zmiana punktu anuluje poprzednie żądanie i usuwa stary wynik. Błąd pobierania
wyświetla komunikat bez wyniku. Żądanie geolokalizacji zakończone po ręcznym
wyborze punktu nie nadpisuje tego wyboru.
