import { useEffect, useState } from 'react'

export default function App() {
  const [apiStatus, setApiStatus] = useState('Sprawdzanie połączenia...')

  useEffect(() => {
    const controller = new AbortController()

    fetch('/api/health', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      })
      .then((data) => setApiStatus(data.status === 'ok' ? 'Połączono' : 'Nieznany stan API'))
      .catch((error) => {
        if (error.name !== 'AbortError') setApiStatus('Brak połączenia z API')
      })

    return () => controller.abort()
  }, [])

  return (
    <main>
      <h1>Copernicus</h1>
      <p>Projekt analizy danych przestrzennych</p>
      <p>Backend: <strong>{apiStatus}</strong></p>
    </main>
  )
}
