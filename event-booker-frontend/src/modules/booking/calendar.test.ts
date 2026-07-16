import { describe, expect, it } from 'vitest'
import { availableDaysFromSlots, dateKey, firstAvailableDay, monthRange, parseDateKey } from './calendar.ts'
import type { Slots } from './types.ts'

const slots = (map: Record<string, string[]>): Slots => ({ event_type_id: 'e1', time_zone: 'UTC', slots: map })

describe('calendar helpers', () => {
  it('dateKey uses local calendar components', () => {
    expect(dateKey(new Date(2026, 9, 1, 23, 30))).toBe('2026-10-01') // month is 0-based → October
    expect(dateKey(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('parseDateKey round-trips with dateKey', () => {
    expect(dateKey(parseDateKey('2026-07-21'))).toBe('2026-07-21')
  })

  it('availableDaysFromSlots keeps only non-empty days', () => {
    const s = availableDaysFromSlots(slots({ '2026-07-21': ['x'], '2026-07-22': [], '2026-07-23': ['y', 'z'] }))
    expect([...s].sort()).toEqual(['2026-07-21', '2026-07-23'])
  })

  it('firstAvailableDay returns the earliest day with slots, or null', () => {
    expect(dateKey(firstAvailableDay(slots({ '2026-07-23': ['y'], '2026-07-21': ['x'] }))!)).toBe('2026-07-21')
    expect(firstAvailableDay(slots({ '2026-07-22': [] }))).toBeNull()
    expect(firstAvailableDay(slots({}))).toBeNull()
  })

  it('monthRange spans one month and clamps the start to now', () => {
    const r = monthRange(new Date(2030, 0, 10), new Date(2029, 0, 1)) // now far before → start = month start
    const days = (new Date(r.endISO).getTime() - new Date(r.startISO).getTime()) / 86_400_000
    expect(days).toBeGreaterThanOrEqual(28)
    expect(days).toBeLessThanOrEqual(31)
    const clamped = monthRange(new Date(2030, 0, 1), new Date(2030, 0, 20)) // now inside month → start = now
    expect(new Date(clamped.startISO).getTime()).toBe(new Date(2030, 0, 20).getTime())
  })
})
