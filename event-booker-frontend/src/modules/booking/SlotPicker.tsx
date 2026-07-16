import { useEffect, useMemo, useState } from 'react'
import { DayPicker } from 'react-day-picker'
import { ru } from 'react-day-picker/locale'
import 'react-day-picker/style.css'
import { getSlots } from './bookerApi.ts'
import { formatDayLabel, formatTime } from './datetime.ts'
import { availableDaysFromSlots, dateKey, firstAvailableDay, monthRange, startOfDay, startOfMonth } from './calendar.ts'
import type { Slots } from './types.ts'

const COMMON_ZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'UTC']

type Props = {
  eventTypeId: string
  eventTitle: string
  durationMinutes: number
  timeZone: string
  onTimeZoneChange: (tz: string) => void
  onSelectSlot: (startTime: string) => void
  initialMonth?: Date
}

type FetchResult = { requestId: string; slots: Slots | null; error: boolean }

export function SlotPicker({
  eventTypeId,
  eventTitle,
  durationMinutes,
  timeZone,
  onTimeZoneChange,
  onSelectSlot,
  initialMonth,
}: Props) {
  const [month, setMonth] = useState<Date>(() => initialMonth ?? startOfMonth(new Date()))
  const [clickedDay, setClickedDay] = useState<Date | null>(null)
  const [result, setResult] = useState<FetchResult>({ requestId: '', slots: null, error: false })

  const { startISO, endISO } = useMemo(() => monthRange(month), [month])
  const requestId = `${eventTypeId}|${timeZone}|${startISO}`

  useEffect(() => {
    let active = true
    getSlots(eventTypeId, startISO, endISO, timeZone)
      .then((s) => active && setResult({ requestId, slots: s, error: false }))
      .catch(() => active && setResult({ requestId, slots: null, error: true }))
    return () => {
      active = false
    }
  }, [eventTypeId, timeZone, startISO, endISO, requestId])

  const isCurrent = result.requestId === requestId
  const slots = isCurrent ? result.slots : null
  const error = isCurrent && result.error

  const availableDays = useMemo(() => (slots ? availableDaysFromSlots(slots) : new Set<string>()), [slots])

  // Effective selection: the user's clicked day if still available, else the first available day.
  const selectedDay = useMemo(() => {
    if (!slots) return null
    if (clickedDay && availableDays.has(dateKey(clickedDay))) return clickedDay
    return firstAvailableDay(slots)
  }, [slots, clickedDay, availableDays])

  const today = startOfDay(new Date())
  const daySlots = slots && selectedDay ? (slots.slots[dateKey(selectedDay)] ?? []) : []
  const zones = COMMON_ZONES.includes(timeZone) ? COMMON_ZONES : [timeZone, ...COMMON_ZONES]

  return (
    <div className="cal-card">
      <div className="cal-info">
        <h2>{eventTitle}</h2>
        <p className="muted">{durationMinutes} мин</p>
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
      </div>

      <div className="cal-grid">
        <DayPicker
          mode="single"
          locale={ru}
          month={month}
          onMonthChange={setMonth}
          startMonth={startOfMonth(new Date())}
          selected={selectedDay ?? undefined}
          onSelect={(d) => d && setClickedDay(d)}
          disabled={(d) => d < today || !availableDays.has(dateKey(d))}
          modifiers={{ available: (d) => availableDays.has(dateKey(d)) }}
          modifiersClassNames={{ available: 'rdp-available' }}
        />
      </div>

      <div className="cal-slots">
        {error && <p className="banner-error">Не удалось загрузить слоты. Попробуйте ещё раз.</p>}
        {!error && slots === null && <p className="muted">Загрузка…</p>}
        {!error && slots !== null && selectedDay === null && <p className="muted">Нет свободных слотов</p>}
        {!error && selectedDay !== null && (
          <>
            <h3 className="cal-day-header">{formatDayLabel(selectedDay)}</h3>
            {daySlots.length === 0 ? (
              <p className="muted">Нет свободных слотов</p>
            ) : (
              <div className="slot-grid">
                {daySlots.map((iso) => (
                  <button key={iso} type="button" className="slot-button" onClick={() => onSelectSlot(iso)}>
                    {formatTime(iso, timeZone)}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
