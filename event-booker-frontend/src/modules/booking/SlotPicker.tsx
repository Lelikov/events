import { useEffect, useMemo, useState } from 'react'
import { getSlots } from './bookerApi.ts'
import { formatDate, formatTime } from './datetime.ts'
import type { Slots } from './types.ts'

const WINDOW_DAYS = 14
const COMMON_ZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'UTC']

type Props = {
  eventTypeId: string
  timeZone: string
  onTimeZoneChange: (tz: string) => void
  onSelect: (startTime: string) => void
}

type FetchResult = { requestId: string; data: Slots | null; error: boolean }

export function SlotPicker({ eventTypeId, timeZone, onTimeZoneChange, onSelect }: Props) {
  const [offsetDays, setOffsetDays] = useState(0)
  const [result, setResult] = useState<FetchResult>({ requestId: '', data: null, error: false })

  const zones = useMemo(
    () => (COMMON_ZONES.includes(timeZone) ? COMMON_ZONES : [timeZone, ...COMMON_ZONES]),
    [timeZone],
  )

  const requestId = `${eventTypeId}|${timeZone}|${offsetDays}`

  useEffect(() => {
    let active = true
    const start = new Date(Date.now() + offsetDays * 86_400_000)
    const end = new Date(start.getTime() + WINDOW_DAYS * 86_400_000)
    getSlots(eventTypeId, start.toISOString(), end.toISOString(), timeZone)
      .then((d) => active && setResult({ requestId, data: d, error: false }))
      .catch(() => active && setResult({ requestId, data: null, error: true }))
    return () => {
      active = false
    }
  }, [eventTypeId, timeZone, offsetDays, requestId])

  const isCurrent = result.requestId === requestId
  const data = isCurrent ? result.data : null
  const error = isCurrent && result.error

  if (error) {
    return <p className="banner-error">Не удалось загрузить слоты. Попробуйте ещё раз.</p>
  }

  const dates = data ? Object.keys(data.slots).sort() : []

  return (
    <div>
      <label className="field">
        <span>Часовой пояс</span>
        <select value={timeZone} onChange={(e) => onTimeZoneChange(e.target.value)}>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
      </label>

      {data === null && <p className="muted">Загрузка…</p>}
      {data !== null && dates.length === 0 && <p className="muted">Нет свободных слотов в этом окне.</p>}

      {dates.map((date) => (
        <section key={date}>
          <h3>{formatDate(data!.slots[date][0], timeZone)}</h3>
          <div className="slot-grid">
            {data!.slots[date].map((iso) => (
              <button key={iso} type="button" className="slot-button" onClick={() => onSelect(iso)}>
                {formatTime(iso, timeZone)}
              </button>
            ))}
          </div>
        </section>
      ))}

      <div className="inline-actions">
        <button type="button" onClick={() => setOffsetDays((o) => o + WINDOW_DAYS)}>
          Позже →
        </button>
        {offsetDays > 0 && (
          <button type="button" onClick={() => setOffsetDays((o) => Math.max(0, o - WINDOW_DAYS))}>
            ← Раньше
          </button>
        )}
      </div>
    </div>
  )
}
