import demoResponse from '../mocks/evaluate.json'

// This is the only data source used by the UI. Replace the implementation
// with POST /api/evaluate when the agreed endpoint is ready (see README.md).
export async function evaluate(point, { signal } = {}) {
  signal?.throwIfAborted()
  if (!Number.isFinite(point?.latitude) || Math.abs(point.latitude) > 90 ||
      !Number.isFinite(point?.longitude) || Math.abs(point.longitude) > 180) {
    throw new Error('Nieprawidłowe współrzędne punktu.')
  }
  return structuredClone(demoResponse)
}
