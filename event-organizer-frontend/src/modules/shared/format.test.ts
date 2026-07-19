import { describe, expect, it } from 'vitest'
import { formatDateTime, formatRange } from './format.ts'

describe('formatDateTime', () => {
  it('returns a dash for null', () => {
    expect(formatDateTime(null)).toBe('—')
  })

  it('formats an ISO string in a fixed zone', () => {
    expect(formatDateTime('2026-07-25T09:00:00Z', 'UTC')).toContain('2026')
  })

  it('falls back to the raw value for an unparseable input', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })

  it('falls back to the default zone for an invalid timeZone', () => {
    expect(formatDateTime('2026-07-25T09:00:00Z', 'Not/AZone')).toContain('2026')
  })
})

describe('formatRange', () => {
  it('joins start and end with an en dash', () => {
    const out = formatRange('2026-07-25T09:00:00Z', '2026-07-25T10:00:00Z', 'UTC')
    expect(out).toContain('–')
  })
})
