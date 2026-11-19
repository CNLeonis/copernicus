# Baza dla frontendowca

Repozytorium zawiera `opole.dump`: poprawioną bazę z rzeczywistymi usługami OSM, Overture i MZK, obrysami budynków, siatkami 250/500 m, cechami GIS, ocenami i 18 przebiegami modeli. Domyślny model to K-Means, 6 liczników, k=2. Nie trzeba pobierać danych ani trenować modeli.

## Uruchomienie po pobraniu zmian

W katalogu głównym projektu, przy uruchomionym Dockerze:

```sh
git pull
docker compose --profile setup run --rm restore-db
docker compose up -d --build backend frontend
```

Mapa: <http://localhost:5173>. API: <http://localhost:8000/docs>. Odśwież otwartą wcześniej stronę mapy.

`restore-db` uruchamia bazę i **zastępuje jej dane przygotowaną kopią**. Przed odtworzeniem zapisuje dotychczasową bazę w `database/backups/` (katalog ignorowany przez Git). Jeżeli wykonanie kopii się nie powiedzie, nie rozpoczyna odtwarzania. Odtwarzanie odbywa się w jednej transakcji.

Po kolejnych zmianach samego frontendu nie trzeba ponownie odtwarzać bazy. Powtórz polecenie `restore-db`, gdy zmieni się `database/opole.dump` albo chcesz wrócić do wspólnego zestawu danych. Samo `git pull` i zwykłe uruchomienie aplikacji nie nadpisują lokalnych danych.

## Sprawdzenie

W API `/api/services?limit=1` pole `total` powinno wynosić **1012**: 292 sklepy, 67 szkół i 653 przystanki (łącznie z buforem wokół miasta). Model sześciu liczników obejmuje 2534 komórki 250 m i 669 komórek 500 m.

Ta kopia zastępuje wcześniejsze zalecenie ponownego importowania danych przez frontendowca. Import z aktualnych źródeł może dać inne wyniki; do wspólnej pracy używamy kopii z repozytorium.

Dane: [OpenStreetMap](https://www.openstreetmap.org/copyright), [Overture Maps](https://docs.overturemaps.org/attribution/), [MZK Opole](https://www.mzkopole.pl/o-firmie/otwarte-dane/). Dane MZK wytworzono 3.11.2026, pobrano 17.11.2026 i przetworzono przez wybór oraz deduplikację przystanków. MZK nie odpowiada za wyniki analizy. Kopia zawiera przetworzone dane publiczne i wyniki projektu, bez danych logowania.
