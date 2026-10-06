import { describe, expect, it } from 'vitest'
import { applicationContextId, withApplicationContext } from './applicationContext'

describe('application workflow context', () => {
  it('reads the active application from the query string', () => {
    expect(applicationContextId('?application=app-1&section=evidence')).toBe('app-1')
  })

  it('adds application context without discarding existing query parameters', () => {
    expect(withApplicationContext('/comparison/role-1?view=gaps', 'app-1')).toBe('/comparison/role-1?view=gaps&application=app-1')
  })

  it('leaves ordinary navigation unchanged outside an application workflow', () => {
    expect(withApplicationContext('/comparison/role-1', null)).toBe('/comparison/role-1')
  })
})
