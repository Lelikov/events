import { formatDayTimeRange, formatWeekday } from './datetime.ts'
import { timeZoneLabel } from './timezones.ts'
import { navigateTo } from '../shared/routing.ts'
import type { BookingConfirmation } from './types.ts'

export function Confirmation({ confirmation }: { confirmation: BookingConfirmation }) {
  return (
    <div className="confirm-card">
      <div className="confirm-emoji" aria-hidden="true">
        ✅
      </div>
      <h1>Встреча забронирована</h1>
      <p className="confirm-title">{confirmation.event_type_title}</p>
      <p className="confirm-weekday">{formatWeekday(confirmation.start_time, confirmation.time_zone)}</p>
      <p className="confirm-time">
        {formatDayTimeRange(confirmation.start_time, confirmation.end_time, confirmation.time_zone)}
      </p>
      <p className="muted">{timeZoneLabel(confirmation.time_zone)}</p>
      <button type="button" onClick={() => navigateTo('/')}>
        На главную
      </button>
    </div>
  )
}
