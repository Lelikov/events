import { useEffect, useState } from 'react'
import { formatDateTime, formatRange } from '../shared/format.ts'
import { getBookingDetail } from './bookingsApi.ts'
import { ReassignModal } from './ReassignModal.tsx'
import { RescheduleModal } from './RescheduleModal.tsx'
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

type Props = { bookingId: string | null; organizerTz: string | undefined; onChanged?: () => void }

// The parent keys this component by the selected booking id, so a new selection
// remounts it with fresh state — no synchronous state reset in the effect.
export function BookingDetailPanel({ bookingId, organizerTz, onChanged }: Props) {
  const [detail, setDetail] = useState<BookingDetail | null>(null)
  const [error, setError] = useState(false)
  const [rescheduling, setRescheduling] = useState(false)
  const [reassigning, setReassigning] = useState(false)

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

  const canModify = detail.status === 'confirmed' && new Date(detail.start_time).getTime() > Date.now()

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <h2>{detail.title}</h2>
        <span className={`badge ${STATUS_VARIANT[detail.status] ?? ''}`}>
          {STATUS_LABEL[detail.status] ?? detail.status}
        </span>
      </div>
      <div className="detail-fields">
        <Field label="Дата и время" value={formatRange(detail.start_time, detail.end_time, organizerTz)} />
        {detail.client_name && <Field label="Клиент" value={detail.client_name} />}
        {detail.client_email && <Field label="Email" value={detail.client_email} />}
        {detail.client_time_zone && <Field label="Часовой пояс клиента" value={detail.client_time_zone} />}
        {detail.created_at && <Field label="Создана" value={formatDateTime(detail.created_at, organizerTz)} />}
      </div>
      {detail.field_answers.length > 0 && (
        <div className="detail-answers">
          <h3>Анкета</h3>
          {detail.field_answers.map((a) => (
            <Field key={a.label} label={a.label} value={a.value} />
          ))}
        </div>
      )}
      {canModify && (
        <div className="detail-actions">
          <button type="button" onClick={() => setRescheduling(true)}>
            Перенести
          </button>
          <button type="button" className="secondary" onClick={() => setReassigning(true)}>
            Переназначить
          </button>
        </div>
      )}
      {rescheduling && (
        <RescheduleModal
          bookingId={detail.id}
          currentStart={detail.start_time}
          organizerTz={organizerTz}
          onClose={() => setRescheduling(false)}
          onRescheduled={() => {
            setRescheduling(false)
            onChanged?.()
          }}
        />
      )}
      {reassigning && (
        <ReassignModal
          bookingId={detail.id}
          onClose={() => setReassigning(false)}
          onReassigned={() => {
            setReassigning(false)
            onChanged?.()
          }}
        />
      )}
    </div>
  )
}
