import { HourSelect } from './HourSelect.tsx'
import { makeUid, type OverrideState } from './schedule.ts'

type Props = {
  overrides: OverrideState[]
  onChange: (overrides: OverrideState[]) => void
}

const EMPTY: Omit<OverrideState, 'uid'> = { date: '', fullDay: false, start: '09:00', end: '18:00' }

export function DateOverrides({ overrides, onChange }: Props) {
  function update(idx: number, next: OverrideState) {
    onChange(overrides.map((o, i) => (i === idx ? next : o)))
  }

  function add() {
    onChange([...overrides, { ...EMPTY, uid: makeUid() }])
  }

  function remove(idx: number) {
    onChange(overrides.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {overrides.map((o, idx) => (
        <div className="override-row" key={o.uid}>
          <input
            type="date"
            className="field-control"
            value={o.date}
            onChange={(e) => update(idx, { ...o, date: e.target.value })}
          />
          {!o.fullDay && (
            <>
              <HourSelect value={o.start} ariaLabel="Начало" onChange={(v) => update(idx, { ...o, start: v })} />
              <span>–</span>
              <HourSelect value={o.end} ariaLabel="Конец" onChange={(v) => update(idx, { ...o, end: v })} />
            </>
          )}
          <label>
            <input
              type="checkbox"
              checked={o.fullDay}
              onChange={(e) =>
                update(idx, e.target.checked ? { ...o, fullDay: true, start: '', end: '' } : { ...o, fullDay: false })
              }
            />{' '}
            весь день недоступен
          </label>
          <button type="button" className="icon-button" aria-label="Удалить дату" onClick={() => remove(idx)}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="link-button" onClick={add}>
        + Добавить дату
      </button>
    </div>
  )
}
