import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { makeUid, type TravelState } from './schedule.ts'

type Props = {
  travels: TravelState[]
  onChange: (travels: TravelState[]) => void
}

const EMPTY: Omit<TravelState, 'uid'> = { start_date: '', end_date: '', time_zone: 'UTC' }

export function Travel({ travels, onChange }: Props) {
  function update(idx: number, next: TravelState) {
    onChange(travels.map((t, i) => (i === idx ? next : t)))
  }

  function add() {
    onChange([...travels, { ...EMPTY, uid: makeUid() }])
  }

  function remove(idx: number) {
    onChange(travels.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {travels.map((t, idx) => (
        <div className="travel-row" key={t.uid}>
          <input type="date" value={t.start_date} onChange={(e) => update(idx, { ...t, start_date: e.target.value })} />
          <span>–</span>
          <input type="date" value={t.end_date} onChange={(e) => update(idx, { ...t, end_date: e.target.value })} />
          <TimeZoneField value={t.time_zone} onChange={(tz) => update(idx, { ...t, time_zone: tz })} />
          <button type="button" className="icon-button" aria-label="Удалить поездку" onClick={() => remove(idx)}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="link-button" onClick={add}>
        + Добавить поездку
      </button>
    </div>
  )
}
