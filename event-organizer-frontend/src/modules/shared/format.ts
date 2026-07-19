export function formatDateTime(value: string | null | undefined, timeZone?: string): string {
  if (value == null) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  const options = { dateStyle: 'medium', timeStyle: 'short' } as const

  try {
    return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone }).format(date)
  } catch {
    return new Intl.DateTimeFormat('ru-RU', options).format(date)
  }
}

export function formatRange(
  start: string | null | undefined,
  end: string | null | undefined,
  timeZone?: string,
): string {
  return `${formatDateTime(start, timeZone)} – ${formatDateTime(end, timeZone)}`
}
