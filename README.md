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

K-Means grupuje komórki o podobnej liczbie usług. Do wskazania ograniczonej dostępności służy osobna, jawna reguła GIS: dla każdej kategorii jest „wysoka”, gdy w promieniu 500 m jest co najmniej jeden obiekt, „średnia”, gdy obiekt jest dopiero w promieniu 1000 m, i „niska”, gdy nie ma go także w promieniu 1000 m. Łączna ocena jest „niska”, jeśli co najmniej dwie z trzech kategorii mają ocenę „niska”. Są to progi przyjęte w projekcie, a nie norma planistyczna. Odległości są euklidesowe; nie uwzględniają tras pieszych ani barier.

| Siatka | Klaster | Komórki | Mediana sklepów / szkół / przystanków do 1000 m | Mediana odległości do najbliższego sklepu / szkoły / przystanku | Ocena niska / średnia / wysoka |
|---:|---|---:|---|---|---:|
| 250 m | 0 — mniej usług | 1973 | 0 / 0 / 5 | 1002 / 1410 / 623 m | 997 / 936 / 40 |
| 250 m | 1 — więcej usług | 561 | 12 / 3 / 27 | 259 / 453 / 191 m | 0 / 83 / 478 |
| 500 m | 1 — mniej usług | 548 | 0 / 0 / 5 | 998 / 1355 / 606 m | 275 / 252 / 21 |
| 500 m | 0 — więcej usług | 121 | 16 / 3 / 29 | 194 / 426 / 177 m | 0 / 14 / 107 |

W siatce 250 m wszystkie 997 komórek z niską oceną znalazło się w klastrze z mniejszą liczbą usług, ale ten klaster obejmuje też 936 komórek z oceną średnią. Klaster nie jest więc etykietą „obszar niedostatecznie wyposażony”. Tylko 229 z tych 997 komórek ma w danych przesłanki potencjalnego zamieszkania; brak takich przesłanek w pozostałych komórkach nie dowodzi, że są niezamieszkane. Bez danych o ludności nie można stwierdzić, ilu mieszkańców dotyczy ograniczona dostępność.

Sześć liczników pozwoliło przypisać wszystkie 2534 analizowane komórki siatki 250 m, podczas gdy model dziewięciu cech przypisał 2063 z nich, ponieważ brakowało części odległości do najbliższych usług. Mediany odległości w tabeli liczone są tylko dla komórek z dostępną odległością. Silhouette wynosi 0,519 dla 250 m i 0,564 dla 500 m w przestrzeni sześciu cech; tych wartości nie porównuje się bezpośrednio z wynikiem dziewięciu cech. Wariant cech wybrano po obejrzeniu tych samych danych, dlatego wyników na odłożonych obszarach nie należy przedstawiać jako niezależnego potwierdzenia przewagi modelu.

## Testy

Backend: w `backend/` zainstaluj `requirements-dev.txt` i uruchom `python -m pytest tests/api tests/pipeline`. Testy z oznaczeniem `db` wymagają PostGIS. Frontend: `npm --prefix frontend test` oraz `npm --prefix frontend run build`.
