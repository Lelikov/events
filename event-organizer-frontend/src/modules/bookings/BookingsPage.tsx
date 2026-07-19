import { useEffect, useState } from 'react'
import { formatRange } from '../shared/format.ts'
import { getProfile } from '../profile/profileApi.ts'
import { getBookings } from './bookingsApi.ts'
import type { BookingRow } from './types.ts'

const STATUS_LABEL: Record<string, string> = {
  confirmed: 'Подтверждена',
  cancelled: 'Отменена',
}

const STATUS_VARIANT: Record<string, string> = {
  confirmed: 'badge--confirmed',
  cancelled: 'badge--cancelled',
}

// Only confirmed/cancelled get a colour; an unknown status falls back to the
// neutral base badge rather than being mislabelled as confirmed (green).
function statusVariant(status: string): string {
  return STATUS_VARIANT[status] ?? ''
}

function BookingList({ rows, timeZone }: { rows: BookingRow[]; timeZone: string | undefined }) {
  if (rows.length === 0) {
    return <div className="empty-state">Нет броней</div>
  }
  return (
    <>
      {rows.map((b) => (
        <div className="booking-row" key={b.id}>
          <span>{formatRange(b.start_time, b.end_time, timeZone)}</span>
          <span className={`badge ${statusVariant(b.status)}`}>{STATUS_LABEL[b.status] ?? b.status}</span>
        </div>
      ))}
    </>
  )
}

export function BookingsPage() {
  const [rows, setRows] = useState<BookingRow[] | null>(null)
  const [timeZone, setTimeZone] = useState<string | undefined>(undefined)
  const [now, setNow] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getBookings(), getProfile().catch(() => null)])
      .then(([bookings, profile]) => {
        if (cancelled) return
        setRows(bookings)
        setTimeZone(profile?.time_zone ?? undefined)
        setNow(Date.now())
      })
      .catch(() => {
        if (!cancelled) setError('Не удалось загрузить брони')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <div className="card">{error}</div>
  if (!rows || now === null) return <div className="card">Загрузка…</div>

  const upcoming = rows.filter((b) => new Date(b.start_time).getTime() >= now)
  const past = rows.filter((b) => new Date(b.start_time).getTime() < now)

  if (rows.length === 0) {
    return (
      <div>
        <div className="page-head">
          <h1>Брони</h1>
        </div>
        <div className="empty-state">У вас пока нет броней</div>
      </div>
    )
  }

  return (
    <div>
      <div className="page-head">
        <h1>Брони</h1>
      </div>
      <div className="booking-group">
        <h2>Предстоящие</h2>
        <BookingList rows={upcoming} timeZone={timeZone} />
      </div>
      <div className="booking-group">
        <h2>Прошедшие</h2>
        <BookingList rows={past} timeZone={timeZone} />
      </div>
    </div>
  )
}
