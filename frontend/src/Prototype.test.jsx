import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import Prototype from './Prototype.jsx'
import { analyzeLocation } from './services/analysis.js'

vi.mock('./components/LocationMap.jsx', () => ({
  default: ({ onSelect }) => <button onClick={() => onSelect({ latitude: 50.67, longitude: 17.92 })}>Wskaż punkt testowy</button>,
}))
vi.mock('./services/analysis.js', () => ({ analyzeLocation: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals() })

it('uses the real API mode and recalculates counts when the radius changes', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ type: 'Feature' }) }))
  analyzeLocation.mockImplementation(async (_, { radiusM }) => ({
    cell_id: 'test-cell', cell_size_m: 250, radius_m: radiusM,
    point_features: radiusM === 250
      ? { shops_within_radius: 0, schools_within_radius: 0, bus_stops_within_radius: 4 }
      : { shops_within_radius: 4, schools_within_radius: 0, bus_stops_within_radius: 2 },
    point_assessment: {
      accessibility_class: radiusM === 250 ? 'low' : 'medium', radius_m: radiusM,
    },
    assessment: { accessibility_class: 'high' },
    clustering: [{ algorithm: 'kmeans', cluster_label: 1 }], warnings: [],
  }))
  render(<Prototype />)
  fireEvent.click(screen.getByText('Wskaż punkt testowy'))
  await screen.findByText('W danych brak w tym promieniu: szkoły.')
  expect(screen.getByText('Średnia')).toBeTruthy()
  expect(analyzeLocation).toHaveBeenCalledWith(
    { latitude: 50.67, longitude: 17.92 }, expect.objectContaining({ source: 'api', radiusM: 500 }),
  )
  fireEvent.change(screen.getByLabelText('Promień wokół punktu'), { target: { value: '250' } })
  await screen.findByText('Usługi do 250 m od punktu')
  await screen.findByText('Ocena punktu w promieniu 250 m')
  expect(screen.getByText('Niska')).toBeTruthy()
  expect(analyzeLocation).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ source: 'api', radiusM: 250 }))
})

it('shows an API error without substituting a demonstration result', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }))
  analyzeLocation.mockRejectedValue(new Error('Punkt znajduje się poza granicą Opola.'))
  render(<Prototype />)
  fireEvent.click(screen.getByText('Wskaż punkt testowy'))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('poza granicą Opola'))
  expect(screen.queryByText('Zapisany wynik komórki 250 m')).toBeNull()
})
