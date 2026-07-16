import type { Slots } from './types.ts'

const pad = (n: number) => String(n).padStart(2, '0')

export function dateKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

export function parseDateKey(key: string): Date {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function monthRange(month: Date, now: Date = new Date()): { startISO: string; endISO: string } {
  const first = startOfMonth(month)
  const start = now.getTime() > first.getTime() ? now : first
  const end = new Date(month.getFullYear(), month.getMonth() + 1, 1)
  return { startISO: start.toISOString(), endISO: end.toISOString() }
}

export function availableDaysFromSlots(slots: Slots): Set<string> {
  return new Set(Object.keys(slots.slots).filter((k) => slots.slots[k].length > 0))
}

export function firstAvailableDay(slots: Slots): Date | null {
  const keys = Object.keys(slots.slots)
    .filter((k) => slots.slots[k].length > 0)
    .sort()
  return keys.length > 0 ? parseDateKey(keys[0]) : null
}
