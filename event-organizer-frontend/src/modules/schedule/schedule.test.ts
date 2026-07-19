import { describe, expect, it } from 'vitest'
import { bundleToState, buildUpsert, buildTravel, validate, emptyDays } from './schedule.ts'
import type { ScheduleBundle } from './types.ts'

const bundle: ScheduleBundle = {
  schedule: { id: '1', owner_user_id: '2', name: 'Моё', time_zone: 'Europe/Moscow' },
  weekly_hours: [
    { day_of_week: 1, start_time: '09:00:00', end_time: '12:00:00' },
    { day_of_week: 1, start_time: '14:00:00', end_time: '18:00:00' },
    { day_of_week: 2, start_time: '09:00:00', end_time: '18:00:00' },
  ],
  date_overrides: [
    { date: '2026-07-25', start_time: '10:00:00', end_time: '14:00:00' },
    { date: '2026-07-26', start_time: null, end_time: null },
  ],
  travel_schedules: [
    { time_zone: 'Asia/Dubai', start_date: '2026-08-01', end_date: '2026-08-10', prev_time_zone: 'Europe/Moscow' },
  ],
}

describe('bundleToState', () => {
  it('maps a null bundle to an empty editor with the default tz', () => {
    const s = bundleToState(null, 'UTC')
    expect(s.timeZone).toBe('UTC')
    expect(s.name).toBe('Моё расписание')
    expect(s.days).toHaveLength(7)
    expect(s.days.every((d) => !d.enabled && d.intervals.length === 0)).toBe(true)
    expect(s.overrides).toEqual([])
    expect(s.travels).toEqual([])
  })

  it('groups weekly hours by day and normalises HH:MM', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.timeZone).toBe('Europe/Moscow')
    expect(s.name).toBe('Моё')
    expect(s.days[0]).toEqual({ enabled: true, intervals: [{ start: '09:00', end: '12:00' }, { start: '14:00', end: '18:00' }] })
    expect(s.days[1]).toEqual({ enabled: true, intervals: [{ start: '09:00', end: '18:00' }] })
    expect(s.days[2].enabled).toBe(false)
  })

  it('maps overrides incl. the full-day block', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.overrides[0]).toEqual({ date: '2026-07-25', fullDay: false, start: '10:00', end: '14:00' })
    expect(s.overrides[1]).toEqual({ date: '2026-07-26', fullDay: true, start: '', end: '' })
  })

  it('maps travel rows', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.travels[0]).toEqual({ start_date: '2026-08-01', end_date: '2026-08-10', time_zone: 'Asia/Dubai' })
  })

  it('maps day_of_week 7 (Sunday) to day index 6', () => {
    const sundayBundle: ScheduleBundle = {
      ...bundle,
      weekly_hours: [{ day_of_week: 7, start_time: '10:00:00', end_time: '11:00:00' }],
    }
    const s = bundleToState(sundayBundle, 'UTC')
    expect(s.days[6]).toEqual({ enabled: true, intervals: [{ start: '10:00', end: '11:00' }] })
  })
})

describe('buildUpsert', () => {
  it('emits weekly_hours only for enabled days and full-day override nulls', () => {
    const s = bundleToState(bundle, 'UTC')
    const body = buildUpsert(s)
    expect(body).toEqual({
      name: 'Моё',
      time_zone: 'Europe/Moscow',
      weekly_hours: [
        { day_of_week: 1, start_time: '09:00', end_time: '12:00' },
        { day_of_week: 1, start_time: '14:00', end_time: '18:00' },
        { day_of_week: 2, start_time: '09:00', end_time: '18:00' },
      ],
      date_overrides: [
        { date: '2026-07-25', start_time: '10:00', end_time: '14:00' },
        { date: '2026-07-26', start_time: null, end_time: null },
      ],
    })
  })

  it('drops intervals of a disabled day', () => {
    const s = bundleToState(null, 'UTC')
    s.days[0] = { enabled: false, intervals: [{ start: '09:00', end: '10:00' }] }
    expect(buildUpsert(s).weekly_hours).toEqual([])
  })
})

describe('buildTravel', () => {
  it('wraps rows in the travel_schedules envelope, prev_time_zone = base tz, empty end → null', () => {
    const s = bundleToState(null, 'Europe/Moscow')
    s.travels = [{ start_date: '2026-08-01', end_date: '', time_zone: 'Asia/Dubai' }]
    expect(buildTravel(s)).toEqual({
      travel_schedules: [
        { time_zone: 'Asia/Dubai', start_date: '2026-08-01', end_date: null, prev_time_zone: 'Europe/Moscow' },
      ],
    })
  })
})

describe('validate', () => {
  const base = () => {
    const s = bundleToState(null, 'Europe/Moscow')
    s.days[0] = { enabled: true, intervals: [{ start: '09:00', end: '12:00' }] }
    return s
  }

  it('passes a valid state', () => {
    expect(validate(base())).toEqual([])
  })

  it('flags an invalid time zone', () => {
    const s = base()
    s.timeZone = 'Not/AZone'
    expect(validate(s).some((e) => e.includes('часовой пояс'))).toBe(true)
  })

  it('flags start >= end', () => {
    const s = base()
    s.days[0].intervals = [{ start: '12:00', end: '09:00' }]
    expect(validate(s).some((e) => e.includes('Пн'))).toBe(true)
  })

  it('flags overlapping intervals within a day', () => {
    const s = base()
    s.days[0].intervals = [{ start: '09:00', end: '12:00' }, { start: '11:00', end: '13:00' }]
    expect(validate(s).some((e) => e.includes('пересек'))).toBe(true)
  })

  it('flags an override with start >= end when not full-day', () => {
    const s = base()
    s.overrides = [{ date: '2026-07-25', fullDay: false, start: '14:00', end: '10:00' }]
    expect(validate(s).some((e) => e.includes('2026-07-25'))).toBe(true)
  })

  it('flags an empty interval time', () => {
    const s = base()
    s.days[0].intervals = [{ start: '', end: '12:00' }]
    expect(validate(s).some((e) => e.includes('заполните время интервала'))).toBe(true)
  })

  it('flags an enabled day with zero intervals', () => {
    const s = base()
    s.days[0] = { enabled: true, intervals: [] }
    expect(validate(s).some((e) => e.includes('добавьте хотя бы один интервал или отключите день'))).toBe(true)
  })

  it('flags an override with an empty date', () => {
    const s = base()
    s.overrides = [{ date: '', fullDay: false, start: '09:00', end: '10:00' }]
    expect(validate(s).some((e) => e.includes('Укажите дату исключения'))).toBe(true)
  })

  it('does not flag adjacent, touching, non-overlapping intervals (half-open overlap check)', () => {
    const s = base()
    s.days[0].intervals = [{ start: '09:00', end: '12:00' }, { start: '12:00', end: '15:00' }]
    expect(validate(s)).toEqual([])
  })

  it('uses emptyDays for 7 disabled days', () => {
    expect(emptyDays()).toHaveLength(7)
  })
})
