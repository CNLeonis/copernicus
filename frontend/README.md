# Frontend Copernicus

Interfejs React + Vite, mapa Leaflet ustawiona początkowo na Opole.

```sh
npm ci
npm run dev
npm run build
```

Kliknij mapę, aby wybrać punkt, lub użyj geolokalizacji. Dla obsługi klawiaturą
ustaw fokus na mapie, przesuń ją strzałkami i naciśnij „Wybierz środek mapy”.
„Wróć do Opola” przywraca widok miasta bez zmiany wybranego punktu.
Geolokalizacja wymaga localhost lub HTTPS; odmowa nie blokuje wyboru na mapie.
Podkład mapy pochodzi z OpenStreetMap i wymaga połączenia z internetem.

## Dane i późniejsze podłączenie API

`src/services/evaluation.js` udostępnia `evaluate({ latitude, longitude },
{ signal })`. Obecnie zwraca wyłącznie kopię `src/mocks/evaluate.json`.
Współrzędne nie wpływają na przykład; cały wynik `mode: "demo"` ma wspólne
oznaczenie i komunikat, że nie dotyczy wybranego punktu. `null` wyświetla
„Ocena jeszcze niedostępna”. Nie przyjmujemy skali punktacji ani jednostek.

Po uzgodnionym wdrożeniu endpointu zastąp implementację `evaluate` wywołaniem:

```js
const response = await fetch('/api/evaluate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(point),
  signal,
})
if (!response.ok) throw new Error(`HTTP ${response.status}`)
return response.json()
```

Kontrakt pozostaje w `../docs/api.md`. Komponenty widoku nie pobierają danych.
Zmiana punktu anuluje poprzednie żądanie i usuwa stary wynik. Błąd pobierania
wyświetla komunikat bez wyniku. Żądanie geolokalizacji zakończone po ręcznym
wyborze punktu nie nadpisuje tego wyboru.
