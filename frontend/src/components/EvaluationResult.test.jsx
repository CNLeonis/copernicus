import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import EvaluationResult from './EvaluationResult.jsx'

test('renders the current API assessment as one rule result', () => {
  render(<EvaluationResult result={{
    distance_method: 'euclidean',
    radius_m: 1000,
    point_features: {},
    assessment: { accessibility_class: 'medium', accessibility_score: 0.5, criteria_results: {} },
    clustering: [],
    warnings: [],
  }} />)

  expect(screen.getByText('Średnia')).toBeTruthy()
  expect(screen.getByText('Brak opublikowanego modelu')).toBeTruthy()
})
