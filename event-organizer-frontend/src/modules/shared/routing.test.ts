import { describe, expect, it } from 'vitest'
import { parseRoute } from './routing.ts'

describe('parseRoute', () => {
  it('parses known routes', () => {
    expect(parseRoute('/login')).toEqual({ name: 'login' })
    expect(parseRoute('/')).toEqual({ name: 'schedule' })
    expect(parseRoute('/bookings')).toEqual({ name: 'bookings' })
    expect(parseRoute('/profile')).toEqual({ name: 'profile' })
  })

  it('returns not-found for unknown paths', () => {
    expect(parseRoute('/nope')).toEqual({ name: 'not-found' })
    expect(parseRoute('/bookings/123')).toEqual({ name: 'not-found' })
  })
})
