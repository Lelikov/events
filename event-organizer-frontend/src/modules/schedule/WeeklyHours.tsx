import { DAY_LABELS, type DayState, type Interval } from './schedule.ts'

type Props = {
  days: DayState[]
  onChange: (days: DayState[]) => void
}

const DEFAULT_INTERVAL: Interval = { start: '09:00', end: '18:00' }

export function WeeklyHours({ days, onChange }: Props) {
  function updateDay(idx: number, next: DayState) {
    const copy = days.map((d, i) => (i === idx ? next : d))
    onChange(copy)
  }

  function toggle(idx: number) {
    const day = days[idx]
    if (day.enabled) {
      updateDay(idx, { enabled: false, intervals: [] })
      return
    }
    updateDay(idx, { enabled: true, intervals: [{ ...DEFAULT_INTERVAL }] })
  }

  function addInterval(idx: number) {
    const day = days[idx]
    updateDay(idx, { ...day, intervals: [...day.intervals, { ...DEFAULT_INTERVAL }] })
  }

  function removeInterval(idx: number, ivIdx: number) {
    const day = days[idx]
    updateDay(idx, { ...day, intervals: day.intervals.filter((_, i) => i !== ivIdx) })
  }

  function setTime(idx: number, ivIdx: number, field: 'start' | 'end', value: string) {
    const day = days[idx]
    const intervals = day.intervals.map((iv, i) => (i === ivIdx ? { ...iv, [field]: value } : iv))
    updateDay(idx, { ...day, intervals })
  }

  return (
    <div>
      {DAY_LABELS.map((label, idx) => {
        const day = days[idx]
        return (
          <div className="weekday-row" key={label}>
            <label className="weekday-name">
              <input type="checkbox" checked={day.enabled} onChange={() => toggle(idx)} />
              {label}
            </label>
            <div>
              {!day.enabled && <span className="muted">Недоступно</span>}
              {day.enabled &&
                day.intervals.map((iv, ivIdx) => (
                  <div className="interval-row" key={ivIdx}>
                    <input
                      type="time"
                      value={iv.start}
                      onChange={(e) => setTime(idx, ivIdx, 'start', e.target.value)}
                    />
                    <span>–</span>
                    <input
                      type="time"
                      value={iv.end}
                      onChange={(e) => setTime(idx, ivIdx, 'end', e.target.value)}
                    />
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Удалить интервал"
                      onClick={() => removeInterval(idx, ivIdx)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              {day.enabled && (
                <button type="button" className="link-button" onClick={() => addInterval(idx)}>
                  + интервал
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
