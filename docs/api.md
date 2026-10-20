# Kontrakt API

Wszystkie endpointy aplikacji mają prefiks `/api` i zwracają JSON. Frontend używa względnych adresów, np. `fetch('/api/health')`; konfiguracja Vite przekazuje je do backendu. Zmiany kontraktu (ścieżka, metoda, dane wejściowe, odpowiedź, błędy) zapisujemy tutaj w tym samym PR co implementacja.

## GET /api/health

Sprawdzenie dostępności API.

Odpowiedź `200 OK`:

```json
{"status":"ok"}
```

## POST /api/evaluate

Kontrakt do równoległej pracy nad interfejsem i API. Endpoint nie jest jeszcze
zaimplementowany; plik `frontend/src/mocks/evaluate.json` zawiera lokalną
odpowiedź demonstracyjną o tym samym formacie.

Żądanie `Content-Type: application/json`:

```json
{"latitude": 50.675, "longitude": 17.921}
```

Współrzędne są liczbami dziesiętnymi w WGS84: `latitude` od -90 do 90,
`longitude` od -180 do 180. Oba pola są wymagane.

Odpowiedź `200 OK`:

```json
{
  "mode": "demo",
  "overall": {"score": null, "assessment": null, "reason": null},
  "categories": {
    "grocery": {"score": null, "assessment": null, "reason": null},
    "transit": {"score": null, "assessment": null, "reason": null},
    "park": {"score": null, "assessment": null, "reason": null},
    "school": {"score": null, "assessment": null, "reason": null}
  }
}
```

`mode` przyjmuje `demo` dla przykładowych danych albo `computed` dla wyników
obliczonych przez backend. `score` jest liczbą lub `null`; skala i sposób
punktowania zostaną ustalone później. `assessment` przyjmuje `good`, `poor`
albo `null`. `reason` jest tekstem lub `null`. `null` oznacza, że dany wynik
nie jest jeszcze dostępny; interfejs pokazuje wtedy „Ocena jeszcze niedostępna”.
Przy `mode: "demo"` interfejs oznacza całą odpowiedź jako demonstracyjną.

Podstawowe błędy: `422 Unprocessable Entity` dla brakujących, nieliczbowych
lub nieprawidłowych współrzędnych. Frontend traktuje każdą odpowiedź inną niż
`2xx` jako błąd żądania i pokazuje komunikat bez wyniku oceny. Szczegółowy
format błędów i dodatkowe pola, takie jak metryki czy pobliskie obiekty,
zostaną uzgodnione w tym pliku przed ich użyciem przez frontend.
