import { useEffect, useState } from 'react'
import LocationMap from './components/LocationMap.jsx'
import { analyzeLocation } from './services/analysis.js'

const categories = [
  ['Sklepy spożywcze', 'shops_within_radius', 'nearest_shop_m'],
  ['Szkoły', 'schools_within_radius', 'nearest_school_m'],
  ['Przystanki', 'bus_stops_within_radius', 'nearest_bus_stop_m'],
]
const classes = { high: 'Wysoka', medium: 'Średnia', low: 'Niska', no_data: 'Brak danych' }

export default function Prototype() {
  const [point, setPoint] = useState(null)
  const [radius, setRadius] = useState(500)
  const [result, setResult] = useState(null)
  const [cell, setCell] = useState(null)
  const [boundary, setBoundary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mapError, setMapError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/boundary', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('Nie udało się pobrać granicy Opola.')
        return response.json()
      })
      .then(setBoundary)
      .catch(error => { if (!controller.signal.aborted) setMapError(error.message) })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!point) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setResult(null)
    setCell(null)
    async function load() {
      try {
        const data = await analyzeLocation(point, {
          source: 'api', radiusM: radius, signal: controller.signal,
        })
        if (controller.signal.aborted) return
        setResult(data)
        const response = await fetch(`/api/cells/${encodeURIComponent(data.cell_id)}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Wynik dostępny, ale nie udało się pobrać obrysu komórki.')
        const geometry = await response.json()
        if (!controller.signal.aborted) setCell(geometry)
      } catch (error) {
        if (!controller.signal.aborted) setError(error.message)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    load()
    return () => controller.abort()
  }, [point, radius])

  function selectPoint(next) {
    setResult(null)
    setCell(null)
    setError('')
    setPoint(next)
  }

  const features = result?.point_features ?? {}
  const absent = categories.filter(([, count]) => features[count] === 0)
  const unknown = categories.filter(([, count]) => features[count] == null)
  const kmeans = result?.clustering?.find(item => item.algorithm === 'kmeans')

  return <main className="prototype">
    <aside className="panel">
      <h1>Dostępność usług w Opolu</h1>
      <p>Prototyp na rzeczywistych danych. Kliknij miejsce na mapie.</p>
      <section>
        <h2>Wybrany punkt</h2>
        <p>{point ? `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}` : 'Nie wybrano punktu.'}</p>
        <label htmlFor="radius">Promień wokół punktu</label>
        <select id="radius" value={radius} onChange={event => {
          setResult(null); setCell(null); setRadius(Number(event.target.value))
        }}>
          {[250, 500, 1000].map(value => <option key={value} value={value}>{value} m</option>)}
        </select>
        <p className="note">Niebieski okrąg: zasięg w linii prostej.</p>
      </section>
      <div aria-live="polite" aria-busy={loading}>
        {loading && <p role="status">Obliczanie dostępności…</p>}
        {error && <p role="alert" className="error">{error}</p>}
        {result && <>
          <section>
            <h2>Usługi do {result.radius_m} m od punktu</h2>
            <table>
              <thead><tr><th>Usługa</th><th>Liczba</th><th>Najbliższa</th></tr></thead>
              <tbody>{categories.map(([name, count, distance]) => <tr key={count}>
                <th>{name}</th><td>{features[count] ?? 'Brak danych'}</td>
                <td>{features[distance] == null ? 'Brak danych' : `${Math.round(features[distance])} m`}</td>
              </tr>)}</tbody>
            </table>
            <p>{absent.length ? `W danych brak w tym promieniu: ${absent.map(([name]) => name.toLowerCase()).join(', ')}.`
              : unknown.length ? 'Braki danych uniemożliwiają pełną ocenę tego promienia.'
                : 'W tym promieniu znaleziono obiekty każdej z trzech kategorii.'}</p>
            {unknown.length > 0 && <p>Brak danych dla: {unknown.map(([name]) => name.toLowerCase()).join(', ')}.</p>}
            <p className="note">Odległość do najbliższej usługi może przekraczać wybrany promień. Brak w danych nie dowodzi rzeczywistego braku usługi.</p>
          </section>
          <section>
            <h2>Ocena punktu w promieniu {result.point_assessment?.radius_m ?? result.radius_m} m</h2>
            <p>Ocena według reguł GIS: <strong>{classes[result.point_assessment?.accessibility_class] ?? 'Brak danych'}</strong>.</p>
            <p className="note">Ocena dotyczy klikniętego punktu i wybranego promienia.</p>
            <details><summary>Jak powstaje ocena?</summary>
              <p>Niska — w wybranym promieniu brakuje co najmniej dwóch kategorii usług; średnia — brakuje jednej; wysoka — znaleziono wszystkie trzy. Jeśli brakuje danych i nie można rozstrzygnąć oceny, wynik to „Brak danych”.</p>
            </details>
          </section>
          <section>
            <h2>Klaster komórki {result.cell_size_m} m</h2>
            <p>K-Means, 6 liczników: {kmeans ? `grupa ${kmeans.cluster_label}` : 'brak przypisania'}.</p>
            <p className="note">Pomarańczowy obrys pokazuje komórkę modelu. Klaster dotyczy komórki, a ocena powyżej — klikniętego punktu. Numer grupy nie oznacza jakości.</p>
          </section>
          {result.warnings?.length > 0 && <p className="error">Wynik zawiera ograniczenia danych. <small>{result.warnings.join(', ')}</small></p>}
        </>}
      </div>
      <p className="note">Analiza dostępności usług nie wyznacza najlepszej działki pod budowę i nie ocenia jakości życia.</p>
      <small>Dane: OSM, Overture Maps i MZK Opole. <a href="https://www.mzkopole.pl/o-firmie/otwarte-dane/">Źródło MZK</a>. Dane MZK przetworzono do analizy przystanków; MZK nie odpowiada za wyniki analizy.</small>
    </aside>
    <div className="map-area">
      <p id="map-help">Kliknij w granicach Opola</p>
      {mapError && <p role="alert" className="error">{mapError}</p>}
      <LocationMap point={point} onSelect={selectPoint} radius={radius} boundary={boundary} cell={cell} />
    </div>
  </main>
}
