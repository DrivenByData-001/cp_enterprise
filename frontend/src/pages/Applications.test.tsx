import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Applications from './Applications'

afterEach(() => cleanup())

describe('Applications placeholder', () => {
  it('is an honest empty state that never implies persisted application workspaces', () => {
    render(<MemoryRouter><Applications /></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Applications' })).toBeTruthy()
    expect(screen.getByText('No application workspaces yet')).toBeTruthy()
  })

  it('directs the user to Opportunities', () => {
    render(<MemoryRouter><Applications /></MemoryRouter>)
    const link = screen.getByText('Browse opportunities').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/opportunities')
  })
})
