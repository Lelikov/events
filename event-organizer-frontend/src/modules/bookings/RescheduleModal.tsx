import { useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { formatDateTime } from '../shared/format.ts'
import { getBookingSlots, rescheduleBooking } from './bookingsApi.ts'

// YYYY-MM-DD of the instant in the organizer's tz (for the date input default).
function localDate(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(iso),
  )
}

type Props = {
  bookingId: string
  currentStart: string
  organizerTz: string | undefined
  onClose: () => void
  onRescheduled: () => void
}

export function RescheduleModal({ bookingId, currentStart, organizerTz, onClose, onRescheduled }: Props) {
  const tz = organizerTz ?? 'UTC'
  const [date, setDate] = useState(() => localDate(currentStart, tz))
  const [slots, setSlots] = useState<string[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    setSlots(null)
    setLoadError(false)
    setPicked(null)
    getBookingSlots(bookingId, date, tz)
      .then((r) => {
        if (!cancelled) setSlots(r.slots)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [bookingId, date, tz])

  async function confirm() {
    if (!picked) return
    setSaving(true)
    setSubmitError(null)
    try {
      await rescheduleBooking(bookingId, picked)
      onRescheduled()
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Не удалось перенести. Попробуйте ещё раз.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div
        className="modal-content leave-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reschedule-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="reschedule-title">Перенести бронь</h2>
        </div>
        <label className="field">
          <span>Дата</span>
          <input type="date" className="field-control" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <div className="slot-grid">
          {slots === null && !loadError && <span className="muted">Загрузка…</span>}
          {loadError && <span className="error-text">Не удалось загрузить слоты</span>}
          {slots !== null && slots.length === 0 && <span className="muted">Нет свободных слотов на эту дату</span>}
          {slots?.map((s) => (
            <button
              type="button"
              key={s}
              className={`slot-chip${picked === s ? ' is-selected' : ''}`}
              onClick={() => setPicked(s)}
            >
              {formatDateTime(s, tz)}
            </button>
          ))}
        </div>
        {submitError && <p className="error-text">{submitError}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="button" onClick={confirm} disabled={!picked || saving}>
            Перенести
          </button>
        </div>
      </div>
    </div>
  )
}
