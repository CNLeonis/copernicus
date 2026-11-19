import demoResponse from '../mocks/analyze-location.json'

// Set VITE_ANALYSIS_SOURCE=api to use stored analyses.
export const analysisSource = import.meta.env.VITE_ANALYSIS_SOURCE === 'api' ? 'api' : 'demo'

export function isValidPoint(point) {
  return Number.isFinite(point?.latitude) && Math.abs(point.latitude) <= 90 &&
    Number.isFinite(point?.longitude) && Math.abs(point.longitude) <= 180
}

export async function analyzeLocation(point, { signal, radiusM = 1000, source = analysisSource } = {}) {
  signal?.throwIfAborted()
  if (!isValidPoint(point)) throw new Error('Nieprawidłowe współrzędne punktu.')
  if (source === 'demo') return structuredClone(demoResponse)

  const response = await fetch('/api/analyze-location', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      latitude: point.latitude,
      longitude: point.longitude,
      distance_method: 'euclidean',
      radius_m: radiusM,
    }),
    signal,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${response.status}`)
  }
  return response.json()
}
