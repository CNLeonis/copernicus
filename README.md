# Wykrywanie obszarów niedostatecznie wyposażonych w usługi

Aplikacja wskazuje obszary Opola o ograniczonej dostępności sklepów spożywczych, szkół lub przystanków komunikacji publicznej na podstawie zaimportowanych danych. Import podstawowy używa OpenStreetMap; punkty sklepów i szkół można uzupełnić z Overture Places, a przystanki z danych MZK Opole. Klastry K-Means/HDBSCAN grupują podobne obszary; niezależne reguły oceniają dostępność. Brak punktu w danych nie dowodzi, że usługi nie ma w rzeczywistości.

## Uruchomienie

```sh
docker compose up --build
```

Interfejs: <http://localhost:5173>. API: <http://localhost:8000/docs>. Domyślnie interfejs pokazuje oznaczone dane demonstracyjne. Po przygotowaniu bazy ustaw `VITE_ANALYSIS_SOURCE=api` w środowisku frontendu, aby korzystać z API.

## Przygotowanie bazy

```sh
docker compose up -d db
docker compose run --rm backend python -m pipeline.migrate
docker compose run --rm backend python -m pipeline.import_osm
docker compose run --rm backend python -m pipeline.grid
docker compose run --rm backend python -m pipeline.features
docker compose run --rm backend python -m pipeline.assessment
docker compose run --rm backend python -m pipeline.ml
```

Krok Overture jest opcjonalny. Po [zainstalowaniu klienta Overture](https://docs.overturemaps.org/getting-data/overturemaps-py/) pobierz plik GeoJSON dla Opola i bufora 2 km. Poniższy prostokąt wyznaczono z lokalnej granicy Opola; rzeczywiste punkty importer przycina do bufora. Po imporcie OSM, a przed obliczeniem cech, uruchom:

```sh
overturemaps download --bbox=17.75157,50.56964,18.06097,50.78796 -f geojson -t place -o backend/data/raw/opole-places.geojson
docker compose run --rm backend python -m pipeline.import_overture /app/data/raw/opole-places.geojson
```

Importer Overture przyjmuje tylko odpowiednie, otwarte punkty z nazwą i dostateczną oceną wiarygodności; odrzuca bliskie duplikaty i niejednoznaczne dopasowania. Dane RSPO nie są jeszcze importowane.

Aktualny [pakiet MZK](https://www.mzkopole.pl/o-firmie/otwarte-dane/) zapisz jako `backend/data/raw/mzk_2026-11-06.zip` i po imporcie OSM uruchom:

```sh
docker compose run --rm backend python -m pipeline.import_mzk /app/data/raw/mzk_2026-11-06.zip
```

Źródłem pochodzenia danych jest Miejski Zakład Komunikacyjny sp. z o.o. w Opolu. Dane wytworzono 3.11.2026, pobrano 17.11.2026 i przetworzono: wybrano przystanki pasażerskie w buforze Opola, pominięto bliskie duplikaty. MZK nie odpowiada za wyniki tej analizy. Po każdym uzupełnieniu usług przelicz cechy, oceny i modele.

Domyślna analiza używa siatki 250 m i K-Means (`k=2`) na sześciu licznikach sklepów, szkół i przystanków w promieniach 500 i 1000 m. Liczniki przechodzą `log1p` i `StandardScaler`; dla porównawczej siatki 500 m używany jest `RobustScaler`. Dla 500 m uruchom ponownie `features --cell-size-m 500`, `assessment --size 500` i `ml --size 500`. Starszy model dziewięciu cech oraz HDBSCAN można nadal uruchomić poleceniem `python -m pipeline.ml --feature-set all_nine --algorithm all --k-values 2 3 4 5 6 7 8`. Zapisane wcześniej wyniki pozostają dostępne po identyfikatorze przebiegu. Pobieranie danych i trening działają offline; kliknięcie mapy ich nie uruchamia. Przed zastosowaniem migracji na istniejącej bazie wykonaj jej kopię.

## Interpretacja klastrów

W aktualnych danych siatki 250 m model sześciu liczników przypisał 1973 komórki do grupy z mniejszą liczbą usług oraz 561 do grupy z większą liczbą usług. Mediany liczników w promieniu 1000 m wynoszą odpowiednio 0/0/5 i 12/3/27 (sklepy/szkoły/przystanki). Dla siatki 500 m liczebności wynoszą 548 i 121, a mediany 0/0/5 i 16/3/29. Klaster opisuje podobieństwo profilu usług, a nie samodzielną ocenę niedoboru: w słabszej grupie 250 m 997 komórek ma niską, a 936 średnią dostępność według odrębnych reguł GIS. Silhouette 0,519 dla 250 m i 0,564 dla 500 m obliczono w sześciowymiarowej przestrzeni cech; nie należy go bezpośrednio zestawiać z wynikiem modelu dziewięciu cech. Wariant cech wybrano po obejrzeniu tych danych, więc wyniki na odłożonych obszarach pozostają oceną eksploracyjną.

## Testy

Backend: w `backend/` zainstaluj `requirements-dev.txt` i uruchom `python -m pytest tests/api tests/pipeline`. Testy z oznaczeniem `db` wymagają PostGIS. Frontend: `npm --prefix frontend test` oraz `npm --prefix frontend run build`.
