import { useEffect, useState } from 'react'
import { formatDateTime, formatRange } from '../shared/format.ts'
import { getBookingDetail } from './bookingsApi.ts'
import type { BookingDetail } from './types.ts'

const STATUS_LABEL: Record<string, string> = { confirmed: 'Подтверждена', cancelled: 'Отменена' }
const STATUS_VARIANT: Record<string, string> = { confirmed: 'badge--confirmed', cancelled: 'badge--cancelled' }

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-field">
      <span className="detail-label">{label}</span>
      <span className="detail-value">{value}</span>
    </div>
  )
}

type Props = { bookingId: string | null; organizerTz: string | undefined }

// The parent keys this component by the selected booking id, so a new selection
// remounts it with fresh state — no synchronous state reset in the effect.
export function BookingDetailPanel({ bookingId, organizerTz }: Props) {
  const [detail, setDetail] = useState<BookingDetail | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!bookingId) return
    let cancelled = false
    getBookingDetail(bookingId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [bookingId])

  if (!bookingId) return <div className="detail-panel detail-empty">Выберите бронь, чтобы увидеть детали</div>
  if (error) return <div className="detail-panel error-text">Не удалось загрузить бронь</div>
  if (!detail) return <div className="detail-panel">Загрузка…</div>

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <h2>{detail.title}</h2>
        <span className={`badge ${STATUS_VARIANT[detail.status] ?? ''}`}>
          {STATUS_LABEL[detail.status] ?? detail.status}
        </span>
      </div>
      <Field label="Дата и время" value={formatRange(detail.start_time, detail.end_time, organizerTz)} />
      {detail.client_name && <Field label="Клиент" value={detail.client_name} />}
      {detail.client_email && <Field label="Email" value={detail.client_email} />}
      {detail.client_time_zone && <Field label="Часовой пояс клиента" value={detail.client_time_zone} />}
      {detail.created_at && <Field label="Создана" value={formatDateTime(detail.created_at, organizerTz)} />}
      {detail.field_answers.length > 0 && (
        <div className="detail-answers">
          <h3>Анкета</h3>
          {detail.field_answers.map((a) => (
            <Field key={a.label} label={a.label} value={a.value} />
          ))}
        </div>
      )}
    </div>
  )
}
