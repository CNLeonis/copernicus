const categories = [
  ['shop', 'Sklepy spożywcze', '01', 'nearest_shop_m', 'shops_within_radius'],
  ['school', 'Szkoły', '02', 'nearest_school_m', 'schools_within_radius'],
  ['bus_stop', 'Przystanki', '03', 'nearest_bus_stop_m', 'bus_stops_within_radius'],
]
const noData = 'Brak danych'
const classes = { high: 'Wysoka', medium: 'Średnia', low: 'Niska', no_data: 'Brak danych' }
const variants = { relative: 'względny', threshold: 'progowy' }

function metres(value) {
  return value === null || value === undefined ? noData : `${Math.round(value)} m`
}

function count(value) {
  return value === null || value === undefined ? noData : String(value)
}

export default function EvaluationResult({ result }) {
  const demo = result.mode === 'demo'
  const features = result.point_features ?? {}
  return (
    <section className={`results ${demo ? 'demo-results' : ''}`} aria-labelledby="results-heading">
      {demo && (
        <div className="demo-banner" role="note">
          <span className="demo-badge">DEMO · CAŁA ODPOWIEDŹ</span>
          <p>To demonstracja interfejsu. Odpowiedź nie zawiera obliczeń dla wybranego punktu. Zmiana punktu nie oblicza oceny.</p>
        </div>
      )}
      <div className="section-heading">
        <div><p className="eyebrow">{demo ? 'Przykładowa odpowiedź' : 'Analiza lokalizacji'}</p><h2 id="results-heading">{demo ? 'Podgląd wyniku' : 'Wynik'}</h2></div>
        <span className="section-note">Odległość {result.distance_method === 'euclidean' ? 'w linii prostej' : result.distance_method} · zasięg {result.radius_m} m</span>
      </div>
      <article className="overall-card">
        <div><span className="eyebrow">Ocena dostępności</span><h3>Jawne kryteria</h3></div>
        {result.assessment.length === 0
          ? <p className="unavailable">Ocena jeszcze niedostępna</p>
          : <dl className="assessment">
            {result.assessment.map((item) => (
              <div key={item.assessment_version}>
                <dt>Wariant {variants[item.variant] ?? item.variant}</dt>
                <dd>{classes[item.accessibility_class] ?? item.accessibility_class}</dd>
              </div>
            ))}
          </dl>}
      </article>
      <div className="category-grid">
        {categories.map(([key, label, number, distanceKey, countKey]) => (
          <article key={key} className="category-card" aria-label={label}>
            <div className="category-meta"><span>{number}</span><span>{key}</span></div>
            <h3>{label}</h3>
            <dl className="assessment">
              <div><dt>Najbliższy obiekt</dt><dd>{metres(features[distanceKey])}</dd></div>
              <div><dt>Liczba w zasięgu {result.radius_m} m</dt><dd>{count(features[countKey])}</dd></div>
            </dl>
          </article>
        ))}
      </div>
      <article className="overall-card">
        <div><span className="eyebrow">Grupowanie ML</span><h3>Klaster komórki</h3></div>
        {result.clustering.length === 0
          ? <p className="unavailable">Brak opublikowanego modelu</p>
          : <dl className="assessment">
            {result.clustering.map((item) => (
              <div key={item.run_id}>
                <dt>{item.algorithm}</dt>
                <dd>{item.is_noise ? 'Szum (nietypowy profil)' : `Grupa ${item.cluster_label}`}</dd>
              </div>
            ))}
          </dl>}
      </article>
      {result.warnings.length > 0 && (
        <ul className="warnings">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      )}
    </section>
  )
}
