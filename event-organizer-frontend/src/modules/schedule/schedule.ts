import type { ScheduleBundle, TravelBody, UpsertScheduleBody } from './types.ts'

export type Interval = { uid: string; start: string; end: string }
export type DayState = { enabled: boolean; intervals: Interval[] }
export type OverrideState = { uid: string; date: string; fullDay: boolean; start: string; end: string }
export type TravelState = { uid: string; start_date: string; end_date: string; time_zone: string }
export type EditorState = {
  name: string
  timeZone: string
  days: DayState[]
  overrides: OverrideState[]
  travels: TravelState[]
}

// Index 0..6 → day_of_week 1..7 (ISO, Mon..Sun).
export const DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

export function emptyDays(): DayState[] {
  return DAY_LABELS.map(() => ({ enabled: false, intervals: [] }))
}

export function makeUid(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2)
}

// "09:00:00" | "09:00" → "09:00".
function hhmm(value: string): string {
  return value.slice(0, 5)
}

export function bundleToState(bundle: ScheduleBundle | null, defaultTz: string): EditorState {
  if (!bundle) {
    return { name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] }
  }

  const days = emptyDays()
  for (const wh of bundle.weekly_hours) {
    const idx = wh.day_of_week - 1
    if (idx < 0 || idx > 6) continue
    days[idx].enabled = true
    days[idx].intervals.push({ uid: makeUid(), start: hhmm(wh.start_time), end: hhmm(wh.end_time) })
  }

  const overrides: OverrideState[] = bundle.date_overrides.map((o) => {
    const fullDay = o.start_time === null || o.end_time === null
    return {
      uid: makeUid(),
      date: o.date,
      fullDay,
      start: fullDay ? '' : hhmm(o.start_time ?? ''),
      end: fullDay ? '' : hhmm(o.end_time ?? ''),
    }
  })

  const travels: TravelState[] = bundle.travel_schedules.map((t) => ({
    uid: makeUid(),
    start_date: t.start_date,
    end_date: t.end_date ?? '',
    time_zone: t.time_zone,
  }))

  return { name: bundle.schedule.name, timeZone: bundle.schedule.time_zone, days, overrides, travels }
}

export function buildUpsert(state: EditorState): UpsertScheduleBody {
  const weekly_hours = state.days.flatMap((day, idx) => {
    if (!day.enabled) return []
    return day.intervals.map((iv) => ({
      day_of_week: idx + 1,
      start_time: iv.start,
      end_time: iv.end,
    }))
  })

  const date_overrides = state.overrides.map((o) => ({
    date: o.date,
    start_time: o.fullDay ? null : o.start,
    end_time: o.fullDay ? null : o.end,
  }))

  return { name: state.name, time_zone: state.timeZone, weekly_hours, date_overrides }
}

export function buildTravel(state: EditorState): TravelBody {
  return {
    travel_schedules: state.travels.map((t) => ({
      time_zone: t.time_zone,
      start_date: t.start_date,
      end_date: t.end_date === '' ? null : t.end_date,
      prev_time_zone: state.timeZone,
    })),
  }
}

function isValidTimeZone(tz: string): boolean {
  if (!tz) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return true
  } catch {
    return false
  }
}

// Half-open overlap on "HH:MM" strings (lexical compare is correct: zero-padded).
function overlaps(intervals: Interval[]): boolean {
  const sorted = [...intervals].sort((a, b) => a.start.localeCompare(b.start))
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].start < sorted[i - 1].end) return true
  }
  return false
}

export function validate(state: EditorState): string[] {
  const errors: string[] = []

  if (!isValidTimeZone(state.timeZone)) {
    errors.push('Укажите корректный часовой пояс')
  }

  state.days.forEach((day, idx) => {
    if (!day.enabled) return
    const label = DAY_LABELS[idx]
    if (day.intervals.length === 0) {
      errors.push(`${label}: добавьте хотя бы один интервал или отключите день`)
      return
    }
    for (const iv of day.intervals) {
      if (!iv.start || !iv.end) {
        errors.push(`${label}: заполните время интервала`)
        continue
      }
      if (iv.start >= iv.end) {
        errors.push(`${label}: начало должно быть раньше конца`)
      }
    }
    if (overlaps(day.intervals)) {
      errors.push(`${label}: интервалы пересекаются`)
    }
  })

  state.overrides.forEach((o) => {
    if (!o.date) {
      errors.push('Укажите дату исключения')
      return
    }
    if (o.fullDay) return
    if (!o.start || !o.end || o.start >= o.end) {
      errors.push(`Исключение ${o.date}: начало должно быть раньше конца`)
    }
  })

  return errors
}
