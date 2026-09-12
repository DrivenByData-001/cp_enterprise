import { describe, expect, it } from 'vitest'
import { getZoomBand, ZOOM_BAND_THRESHOLDS } from './vocabConstants'

describe('getZoomBand', () => {
  it('returns far below the medium threshold', () => {
    expect(getZoomBand(0)).toBe('far')
    expect(getZoomBand(ZOOM_BAND_THRESHOLDS.medium - 0.01)).toBe('far')
  })

  it('returns medium at and above the medium threshold, below the close threshold', () => {
    expect(getZoomBand(ZOOM_BAND_THRESHOLDS.medium)).toBe('medium')
    expect(getZoomBand(ZOOM_BAND_THRESHOLDS.close - 0.01)).toBe('medium')
  })

  it('returns close at and above the close threshold', () => {
    expect(getZoomBand(ZOOM_BAND_THRESHOLDS.close)).toBe('close')
    expect(getZoomBand(2.5)).toBe('close')
  })
})
