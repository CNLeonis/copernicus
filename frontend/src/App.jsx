import { useEffect, useRef, useState } from 'react'
import LocationMap from './components/LocationMap.jsx'
import EvaluationResult from './components/EvaluationResult.jsx'
import { analyzeLocation } from './services/analysis.js'

export default function App() {
  const [point, setPoint] = useState(null)
  const [locating, setLocating] = useState(false)
  const [locationError, setLocationError] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const locationRequest = useRef(0)
  const evaluationRequest = useRef(null)

  useEffect(() => () => {
    locationRequest.current += 1
    evaluationRequest.current?.abort()
  }, [])

  function selectPoint(nextPoint) {
    locationRequest.current += 1
    evaluationRequest.current?.abort()
    setLocating(false)
    setLocationError('')
    setPoint(nextPoint)
    setResult(null)
    setError('')
    setLoading(false)
  }

  function locate() {
    if (!navigator.geolocation) {
      setLocationError('Ta przeglądarka nie obsługuje geolokalizacji. Wskaż punkt na mapie.')
      return
    }
    const request = ++locationRequest.current
    setLocating(true)
    setLocationError('')
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        if (request !== locationRequest.current) return
        selectPoint({ latitude: coords.latitude, longitude: coords.longitude })
      },
      (geoError) => {
        if (request !== locationRequest.current) return
        setLocating(false)
        setLocationError(geoError.code === 1
          ? 'Odmówiono dostępu do lokalizacji. Możesz nadal wskazać punkt na mapie.'
          : geoError.code === 3
            ? 'Przekroczono czas ustalania lokalizacji. Spróbuj ponownie lub wskaż punkt na mapie.'
            : 'Nie udało się ustalić lokalizacji. Spróbuj ponownie lub wskaż punkt na mapie.')
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 },
    )
  }

  async function loadEvaluation() {
    evaluationRequest.current?.abort()
    const controller = new AbortController()
    evaluationRequest.current = controller
    setLoading(true)
    setResult(null)
    setError('')
    try {
      const response = await analyzeLocation(point, { signal: controller.signal })
      if (!controller.signal.aborted) setResult(response)
    } catch (requestError) {
      if (!controller.signal.aborted) setError('Nie udało się pobrać oceny. Spróbuj ponownie.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  return (
    <>
      <header className="site-header">
        <a className="brand" href="#main"><span className="brand-symbol" aria-hidden="true">c.</span>copernicus</a>
        <span className="city-label"><span aria-hidden="true">●</span> Opole, Polska</span>
      </header>
      <main id="main">
        <section className="intro" aria-labelledby="page-heading">
          <p className="eyebrow">Twoje miejsce w mieście</p>
          <h1 id="page-heading">Wybierz punkt.<br /><span>Poznaj okolicę.</span></h1>
          <p>Mapa Opola i podgląd dostępności sklepów, szkół i przystanków.</p>
        </section>
        <section className="explorer" aria-label="Wybór lokalizacji">
          <LocationMap point={point} onSelect={selectPoint} />
          <aside className="location-panel">
            <p className="eyebrow">01 / Lokalizacja</p>
            <h2>Zacznij od miejsca</h2>
            <p id="map-help">Kliknij na mapie, aby ustawić marker. Możesz też przesunąć mapę strzałkami i wybrać jej środek.</p>
            <button type="button" className="location-button" onClick={locate} disabled={locating}>
              <span aria-hidden="true">⌖</span> {locating ? 'Ustalanie lokalizacji…' : 'Użyj mojej lokalizacji'}
            </button>
            {locationError && <p className="error-message" role="alert">{locationError}</p>}
            <div className="point-info" role="status" aria-live="polite" aria-atomic="true">
              <span className="point-label">{point ? 'Wybrany punkt' : 'Nie wybrano punktu'}</span>
              {point ? <dl className="coordinates">
                <div><dt>Szerokość</dt><dd>{point.latitude.toFixed(5)}</dd></div>
                <div><dt>Długość</dt><dd>{point.longitude.toFixed(5)}</dd></div>
              </dl> : <p>Wskaż dowolne miejsce na mapie.</p>}
            </div>
            <div className="demo-action">
              <button type="button" className="primary-button" disabled={!point || loading} onClick={loadEvaluation}>
                {loading ? 'Wczytywanie…' : 'Pokaż odpowiedź demo'} <span aria-hidden="true">↗</span>
              </button>
              <p>Tryb demonstracyjny. Przykładowa odpowiedź nie jest oceną wybranego punktu.</p>
            </div>
          </aside>
        </section>
        <div aria-live="polite" aria-busy={loading}>
          {error && <p className="error-message" role="alert">{error}</p>}
          {result ? <EvaluationResult result={result} /> : <section className="empty-result"><span aria-hidden="true">◎</span><p>{loading ? 'Wczytywanie odpowiedzi demonstracyjnej…' : 'Tutaj pojawi się podgląd odpowiedzi demonstracyjnej.'}</p></section>}
        </div>
      </main>
      <footer><span>copernicus / Opole</span><span>Odkrywaj miasto, punkt po punkcie.</span></footer>
    </>
  )
}
