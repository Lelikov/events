import { HOUR_OPTIONS } from './schedule.ts'

type Props = {
  value: string
  onChange: (v: string) => void
  ariaLabel?: string
}

// Whole-hour picker. Rendering a native <select> is both the "looks like a
// select" fix and the "кратно часу" constraint. A legacy off-grid value (e.g.
// "09:30" from older data) is kept as an extra option so it isn't silently
// dropped on load; picking any real option snaps to a whole hour.
export function HourSelect({ value, onChange, ariaLabel }: Props) {
  const offGrid = value !== '' && !HOUR_OPTIONS.includes(value)
  return (
    <select
      className="field-control field-control--select"
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {offGrid && <option value={value}>{value}</option>}
      {HOUR_OPTIONS.map((h) => (
        <option key={h} value={h}>
          {h}
        </option>
      ))}
    </select>
  )
}
