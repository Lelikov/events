// Full IANA time-zone list with Russian, offset-prefixed labels. Built once
// from the platform's Intl data so it stays complete and localized.

export type TimeZoneOption = { id: string; label: string }

const FALLBACK_IDS = ['UTC', 'Europe/Kaliningrad', 'Europe/Moscow', 'Asia/Yekaterinburg', 'Asia/Novosibirsk']

const RU_CITY: Record<string, string> = {
  'Europe/Kaliningrad': 'Калининград',
  'Europe/Moscow': 'Москва',
  'Europe/Simferopol': 'Симферополь',
  'Europe/Volgograd': 'Волгоград',
  'Europe/Kirov': 'Киров',
  'Europe/Astrakhan': 'Астрахань',
  'Europe/Saratov': 'Саратов',
  'Europe/Ulyanovsk': 'Ульяновск',
  'Europe/Samara': 'Самара',
  'Asia/Yekaterinburg': 'Екатеринбург',
  'Asia/Omsk': 'Омск',
  'Asia/Novosibirsk': 'Новосибирск',
  'Asia/Barnaul': 'Барнаул',
  'Asia/Tomsk': 'Томск',
  'Asia/Novokuznetsk': 'Новокузнецк',
  'Asia/Krasnoyarsk': 'Красноярск',
  'Asia/Irkutsk': 'Иркутск',
  'Asia/Chita': 'Чита',
  'Asia/Yakutsk': 'Якутск',
  'Asia/Khandyga': 'Хандыга',
  'Asia/Vladivostok': 'Владивосток',
  'Asia/Ust-Nera': 'Усть-Нера',
  'Asia/Magadan': 'Магадан',
  'Asia/Sakhalin': 'Южно-Сахалинск',
  'Asia/Srednekolymsk': 'Среднеколымск',
  'Asia/Kamchatka': 'Петропавловск-Камчатский',
  'Asia/Anadyr': 'Анадырь',
  'Europe/Kyiv': 'Киев',
  'Europe/Minsk': 'Минск',
  'Europe/Chisinau': 'Кишинёв',
  'Asia/Baku': 'Баку',
  'Asia/Yerevan': 'Ереван',
  'Asia/Tbilisi': 'Тбилиси',
  'Asia/Almaty': 'Алматы',
  'Asia/Tashkent': 'Ташкент',
  'Asia/Bishkek': 'Бишкек',
  'Asia/Ashgabat': 'Ашхабад',
  'Asia/Dushanbe': 'Душанбе',
  'Europe/London': 'Лондон',
  'Europe/Paris': 'Париж',
  'Europe/Berlin': 'Берлин',
  'Europe/Rome': 'Рим',
  'Europe/Madrid': 'Мадрид',
  'Europe/Amsterdam': 'Амстердам',
  'Europe/Istanbul': 'Стамбул',
  'Asia/Dubai': 'Дубай',
  'Asia/Jerusalem': 'Иерусалим',
  'Asia/Bangkok': 'Бангкок',
  'Asia/Shanghai': 'Пекин',
  'Asia/Tokyo': 'Токио',
  'America/New_York': 'Нью-Йорк',
  'America/Los_Angeles': 'Лос-Анджелес',
}

function offsetString(id: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: id, timeZoneName: 'longOffset' }).formatToParts(new Date())
    const value = parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
    return value.replace('GMT', '')
  } catch {
    return ''
  }
}

function offsetMinutes(off: string): number {
  const m = /([+-])(\d{2}):(\d{2})/.exec(off)
  if (!m) return 0
  const sign = m[1] === '-' ? -1 : 1
  return sign * (Number(m[2]) * 60 + Number(m[3]))
}

function russianName(id: string): string {
  try {
    const parts = new Intl.DateTimeFormat('ru-RU', { timeZone: id, timeZoneName: 'long' }).formatToParts(new Date())
    const value = parts.find((p) => p.type === 'timeZoneName')?.value
    if (value) return value.replace(/,\s*(стандартное|летнее)\s+время$/i, '')
  } catch {
    // fall through to the id-derived name
  }
  return cityFromId(id)
}

function cityFromId(id: string): string {
  return id.split('/').pop()?.replace(/_/g, ' ') ?? id
}

export function timeZoneLabel(id: string): string {
  return RU_CITY[id] ?? withCityHint(id)
}

function withCityHint(id: string): string {
  const name = russianName(id)
  const city = cityFromId(id)
  return name.toLowerCase() === city.toLowerCase() ? name : `${name} · ${city}`
}

function allZoneIds(): string[] {
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf
    if (typeof supported === 'function') {
      const list = supported('timeZone')
      if (Array.isArray(list) && list.length > 0) return list
    }
  } catch {
    // fall through to the curated fallback
  }
  return FALLBACK_IDS
}

let cache: TimeZoneOption[] | null = null

export function listTimeZones(): TimeZoneOption[] {
  if (cache) return cache
  const ranked = allZoneIds().map((id) => {
    const off = offsetString(id)
    return { id, label: timeZoneLabel(id), rank: offsetMinutes(off) }
  })
  ranked.sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label, 'ru'))
  cache = ranked.map(({ id, label }) => ({ id, label }))
  return cache
}
