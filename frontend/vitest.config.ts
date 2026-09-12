import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Minimal Vitest setup (no dedicated frontend test framework existed before
// this pass — docs/25-vocabulary-map.md). Scope is deliberately narrow: pure
// helper logic (getZoomBand) and VocabularyMapView's own state transitions
// (fullscreen/details/selection/filters), with `VocabularyGraph` mocked out
// — real React Flow rendering (zoom bands, labels, tooltips, fit-view) is
// covered by browser smoke testing instead, not jsdom.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
