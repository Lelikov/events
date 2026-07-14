import { formatRange } from './datetime.ts'
import { navigateTo } from '../shared/routing.ts'
import type { BookingConfirmation } from './types.ts'

export function Confirmation({ confirmation }: { confirmation: BookingConfirmation }) {
  return (
    <div>
      <h1>Встреча забронирована</h1>
      <p>
        <strong>{confirmation.event_type_title}</strong>
      </p>
      <p>{formatRange(confirmation.start_time, confirmation.end_time, confirmation.time_zone)}</p>
      <p className="muted">Часовой пояс: {confirmation.time_zone}</p>
      <div className="inline-actions">
        <button type="button" onClick={() => navigateTo('/')}>
          На главную
        </button>
      </div>
    </div>
  )
}
