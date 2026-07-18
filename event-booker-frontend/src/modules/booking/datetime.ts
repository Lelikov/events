export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone }).format(new Date(iso))
}

export function formatDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short', timeZone }).format(
    new Date(iso),
  )
}

export function formatRange(startIso: string, endIso: string, timeZone: string): string {
  return `${formatDate(startIso, timeZone)}, ${formatTime(startIso, timeZone)}–${formatTime(endIso, timeZone)}`
}

export function formatDayLabel(d: Date): string {
  return new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }).format(d)
}

// The slots API returns only start instants; the end is start + the event
// duration. Kept as an absolute instant so formatting stays DST-correct.
export function addMinutes(iso: string, minutes: number): string {
  return new Date(new Date(iso).getTime() + minutes * 60000).toISOString()
}

export function formatWeekday(iso: string, timeZone: string): string {
  const w = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', timeZone }).format(new Date(iso))
  return w.charAt(0).toUpperCase() + w.slice(1)
}

// Date + time range without the weekday (the weekday is shown on its own line).
export function formatDayTimeRange(startIso: string, endIso: string, timeZone: string): string {
  const day = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', timeZone }).format(new Date(startIso))
  return `${day}, ${formatTime(startIso, timeZone)}–${formatTime(endIso, timeZone)}`
}
