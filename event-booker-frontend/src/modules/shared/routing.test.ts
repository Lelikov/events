import { describe, expect, it } from 'vitest'
import { parseRoute } from './routing'

describe('parseRoute', () => {
  it('maps / and /event-types to event-types', () => {
    expect(parseRoute('/')).toEqual({ name: 'event-types' })
    expect(parseRoute('/event-types')).toEqual({ name: 'event-types' })
  })
  it('maps /book/{id} to book with the id', () => {
    expect(parseRoute('/book/abc-123')).toEqual({ name: 'book', eventTypeId: 'abc-123' })
  })
  it('maps unknown paths to not-found', () => {
    expect(parseRoute('/whatever')).toEqual({ name: 'not-found' })
  })
})
