const categories = [
  ['grocery', 'Sklepy spożywcze', '01'],
  ['transit', 'Transport publiczny', '02'],
  ['park', 'Parki i zieleń', '03'],
  ['school', 'Szkoły', '04'],
]
const unavailable = 'Ocena jeszcze niedostępna'
const assessments = { good: 'Dobra', poor: 'Słaba' }

function Assessment({ value }) {
  if (value.score === null && value.assessment === null && value.reason === null) {
    return <p className="unavailable">{unavailable}</p>
  }
  return (
    <dl className="assessment">
      <div><dt>Wynik</dt><dd>{value.score ?? unavailable}</dd></div>
      <div><dt>Ocena</dt><dd>{value.assessment === null ? unavailable : assessments[value.assessment]}</dd></div>
      <div><dt>Uzasadnienie</dt><dd>{value.reason ?? unavailable}</dd></div>
    </dl>
  )
}

export default function EvaluationResult({ result }) {
  const demo = result.mode === 'demo'
  return (
    <section className={`results ${demo ? 'demo-results' : ''}`} aria-labelledby="results-heading">
      {demo && (
        <div className="demo-banner" role="note">
          <span className="demo-badge">DEMO · CAŁA ODPOWIEDŹ</span>
          <p>To demonstracja interfejsu. Poniższe dane są przykładowe i nie dotyczą wybranego punktu. Zmiana punktu nie oblicza oceny.</p>
        </div>
      )}
      <div className="section-heading">
        <div><p className="eyebrow">{demo ? 'Przykładowa odpowiedź' : 'Ocena lokalizacji'}</p><h2 id="results-heading">{demo ? 'Podgląd wyniku' : 'Wynik'}</h2></div>
        <span className="section-note">4 kategorie codzienności</span>
      </div>
      <article className="overall-card">
        <div><span className="eyebrow">Podsumowanie</span><h3>Ocena ogólna</h3></div>
        <Assessment value={result.overall} />
      </article>
      <div className="category-grid">
        {categories.map(([key, label, number]) => (
          <article key={key} className="category-card" aria-label={label}>
            <div className="category-meta"><span>{number}</span><span>{key}</span></div>
            <h3>{label}</h3>
            <Assessment value={result.categories[key]} />
          </article>
        ))}
      </div>
    </section>
  )
}
