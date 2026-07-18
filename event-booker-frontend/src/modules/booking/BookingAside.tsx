// Shared left-column context panel for the booking wizard. Rendered identically
// on the slot step (SlotPicker) and the details step (GuestForm) so the event
// title / duration / time-zone block looks the same and does not shift between
// steps. The details step additionally passes `selectedLabel` (the chosen time).
import { TimeZoneField } from './TimeZoneField.tsx'

type Props = {
  eventTitle: string
  durationMinutes: number
  timeZone: string
  // Omit to render the time zone read-only (e.g. on the details step, where the
  // zone was already chosen on the slot step). Present → editable autocomplete.
  onTimeZoneChange?: (tz: string) => void
  selectedLabel?: string
}

export function BookingAside({ eventTitle, durationMinutes, timeZone, onTimeZoneChange, selectedLabel }: Props) {
  return (
    <aside className="booking-aside">
      <h2>{eventTitle}</h2>
      <p className="muted">{durationMinutes} мин</p>
      <label className="field">
        <span>Часовой пояс</span>
        <TimeZoneField value={timeZone} onChange={onTimeZoneChange} />
      </label>
      {selectedLabel && (
        <div className="booking-aside-meta">
          <p className="muted">Выбрано</p>
          <p className="booking-aside-time">{selectedLabel}</p>
        </div>
      )}
    </aside>
  )
}
