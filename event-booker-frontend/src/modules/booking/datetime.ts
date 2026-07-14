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
