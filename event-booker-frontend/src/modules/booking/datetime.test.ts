import { describe, expect, it } from 'vitest'
import { addMinutes } from './datetime.ts'

describe('addMinutes', () => {
  it('adds the duration to the start instant', () => {
    expect(addMinutes('2026-07-20T06:30:00.000Z', 30)).toBe('2026-07-20T07:00:00.000Z')
  })

  it('crosses the hour and day boundary', () => {
    expect(addMinutes('2026-07-20T23:45:00.000Z', 30)).toBe('2026-07-21T00:15:00.000Z')
  })
})
