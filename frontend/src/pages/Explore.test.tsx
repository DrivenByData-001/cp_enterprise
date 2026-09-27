import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Explore from './Explore'

afterEach(() => cleanup())

describe('Explore my future', () => {
  it('links every existing future-oriented tool in plain language', () => {
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Explore my future' })).toBeTruthy()
    expect((screen.getByText('Preferences →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/preferences')
    expect((screen.getByText('Targets →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/targets')
    expect((screen.getByText('Pathways →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/pathways')
    expect((screen.getByText('Trends →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/trends')
    expect((screen.getByText('Economics →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/economics')
    expect((screen.getByText('Career space →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/space')
  })

  it('states that existing Targets are not yet the future property-driven target-discovery system', () => {
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(screen.getByText(/A fuller property-driven career-direction builder will come later/)).toBeTruthy()
  })

  it('distinguishes the observed corpus from the wider market', () => {
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(screen.getByText(/not a claim that the stored corpus is fully representative/)).toBeTruthy()
  })

  it('states that the Career Space visualization is not a recommendation', () => {
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(screen.getByText(/it is not a career recommendation/)).toBeTruthy()
  })
})
