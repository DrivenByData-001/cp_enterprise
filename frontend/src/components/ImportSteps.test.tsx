import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import ImportSteps from './ImportSteps'

afterEach(() => { cleanup() })

// Explicit Role Save brief: Save posting is a real persistence checkpoint —
// once you've moved past it, it must look visibly *done*, not merely "not
// the current step" (which is indistinguishable from a step you haven't
// reached yet).
describe('ImportSteps', () => {
  it('marks steps before the current one as completed, the current one as active, and later ones as neither', () => {
    render(<ImportSteps step={2} />)

    const save = screen.getByText(/Save posting/)
    const details = screen.getByText(/Review details/)
    const requirements = screen.getByText(/Review requirements/)
    const compare = screen.getByText(/Compare/)

    expect(save.className).toContain('completed')
    expect(details.className).toContain('completed')
    expect(requirements.getAttribute('aria-current')).toBe('step')
    expect(requirements.className).not.toContain('completed')
    expect(compare.className).not.toContain('completed')
    expect(compare.getAttribute('aria-current')).toBeNull()
  })

  it('marks nothing as completed on the very first step', () => {
    render(<ImportSteps step={0} />)
    const save = screen.getByText(/Save posting/)
    expect(save.getAttribute('aria-current')).toBe('step')
    expect(save.className).not.toContain('completed')
  })
})
