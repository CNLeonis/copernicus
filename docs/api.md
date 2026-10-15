# Kontrakt API

Wszystkie endpointy aplikacji mają prefiks `/api` i zwracają JSON. Frontend używa względnych adresów, np. `fetch('/api/health')`; konfiguracja Vite przekazuje je do backendu. Zmiany kontraktu (ścieżka, metoda, dane wejściowe, odpowiedź, błędy) zapisujemy tutaj w tym samym PR co implementacja.

## GET /api/health

Sprawdzenie dostępności API.

Odpowiedź `200 OK`:

```json
{"status":"ok"}
```

## Kolejne endpointy

Przed równoległą implementacją warto dopisać dla każdego endpointu przykładowe żądanie, odpowiedź i możliwe błędy. Pozwoli to rozwijać interfejs bez czekania na gotowy backend.
