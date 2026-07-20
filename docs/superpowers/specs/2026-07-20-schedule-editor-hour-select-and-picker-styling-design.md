# Schedule editor: whole-hour time select + picker styling

**Date:** 2026-07-20
**Status:** approved-pending-review (user stepped away mid-brainstorm; design locked with the conservative option, see Decision Log)

## Problem

In the organizer cabinet schedule editor (`event-organizer-frontend`), the
time and date pickers are bare native controls (`<input type="time">`,
`<input type="date">`) sitting in flex rows with no box styling. Next to the
design-system selects / time-zone field (`.field select`, `.tz-picker-input` —
clean bordered box, `bg-soft`, focus ring, chevron) they look out of place.

Two asks:

1. Bring the time/date controls to the same visual style as the selects.
2. Times should be selectable only in **whole-hour** steps ("кратно часу"),
   and this must **also be validated on the backend at save**.

## Scope

- `event-organizer-frontend` — the schedule editor UI (weekly hours, date
  overrides, travel rows) and its CSS.
- `event-scheduling` — the domain owner of the schedule write path; add the
  whole-hour invariant to its save-time validation.

Out of scope: the public Booker, the admin frontend, any other time inputs in
the app; changing the on-the-wire schedule contract shape (times stay `HH:MM`
strings from the SPA, `time` in the domain).

## Design

### 1. Time → whole-hour `<select>` (frontend)

Replace every `<input type="time">` in the schedule editor with a native
`<select>` of whole hours. This is simultaneously the "looks like a select"
fix and the "whole hours only" constraint.

New component `src/modules/schedule/HourSelect.tsx`:

- Props: `{ value: string; onChange: (v: string) => void; ariaLabel?: string }`.
- Renders `<select className="field-control field-control--select">` with 24
  options `"00:00" … "23:00"` (value === label === `HH:00`).
- **Legacy off-grid safety:** if `value` is a non-empty string that is not one
  of the 24 whole-hour options (e.g. pre-existing `"09:30"` data), prepend it
  as an extra `<option>` so the current value renders as selected and is not
  silently dropped. Selecting any real option snaps to a whole hour.

Used in:

- `WeeklyHours.tsx` — the start/end pair per interval (replaces the two
  `input[type="time"]`).
- `DateOverrides.tsx` — the start/end pair shown when the override is not a
  full-day block (replaces the two `input[type="time"]`).

No change to `schedule.ts` types (`Interval`/`OverrideState` keep
`start`/`end: string`) or to `buildUpsert` (still emits `HH:MM`).

### 2. Date → styled native `<input type="date">` (frontend)

Keep the native date input and its native calendar popup; give it the same box
styling as the selects. Add a shared class `field-control` to the date inputs
in `DateOverrides.tsx` (the date field) and `Travel.tsx` (start/end dates).

### 3. Shared control styling (frontend CSS, `index.css`)

```css
/* Row-level controls (hour select, date input) share the design-system field
   box so they sit consistently next to the tz picker / selects. */
.field-control {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 9px 12px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.field-control:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(79, 110, 242, 0.12);
}
/* Select variant: chevron like the design-system .field select. */
.field-control--select {
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236b7280'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 30px;
  cursor: pointer;
}
```

The existing `.interval-row input[type="time"] { width: 120px }` rule is
removed (there is no more `input[type="time"]`); the hour select sizes to its
content. Date inputs size to content.

### 4. Whole-hour validation (backend, `event-scheduling/validation.py`)

`upsert_schedule` already calls `validate_weekly_hours(dto.weekly_hours)` and
`validate_date_overrides(dto.date_overrides)`. Extend both:

```python
def _is_whole_hour(t: time) -> bool:
    return t.minute == 0 and t.second == 0 and t.microsecond == 0
```

- `validate_weekly_hours`: for each row, reject if `start_time` or `end_time`
  is not a whole hour → `ValidationError("weekly_hours times must be on the
  hour (day {day_of_week})")`.
- `validate_date_overrides`: in the `both_set` branch, reject if `start_time`
  or `end_time` is not a whole hour → `ValidationError("date_override {date}:
  times must be on the hour")`. `null` times (full-day block) stay valid.

`ValidationError` → HTTP 422 (existing error mapping). No new endpoint, no
contract shape change. The organizer BFF proxies `PUT /api/me/schedule`
unchanged; a 422 bubbles up as an `ApiError` and the schedule editor's existing
`.error-text` surfaces it.

## Data flow

Unchanged. SPA holds `HH:MM` strings; on save `buildUpsert` posts them to the
BFF `PUT /api/me/schedule`, which forwards to `event-scheduling`
`PUT /api/v1/schedules/{owner}`; the new validator gate runs before the
replace-in-transaction write.

## Error handling

- Frontend: the select cannot produce a non-whole-hour value, so the happy path
  never trips the backend rule. The rule is a guard against non-UI callers and
  legacy off-grid data (which the UI preserves but flags implicitly by letting
  the user re-pick).
- Backend: non-whole-hour input from any client → 422 with a specific message.

## Testing

- **Backend** (`event-scheduling/tests/test_validation.py` or the existing
  validation test module): `validate_weekly_hours` rejects `09:30`, accepts
  `09:00`; `validate_date_overrides` rejects a `both_set` override with a
  `:30` time, accepts whole hours, still accepts `null/null` full-day.
- **Frontend**:
  - `HourSelect.test.tsx`: renders 24 whole-hour options; a value of `09:30`
    is preserved as a selected option; `onChange` fires the picked value.
  - `WeeklyHours.test.tsx` / `DateOverrides.test.tsx`: update any assertions
    that queried `input[type="time"]` to the new select; confirm start/end
    still round-trip into state.

## Decision Log

- **Whole hours only (00:00–23:00), not half-hours** — the user said "кратно
  часу". Max end is `23:00`; the domain `time` column cannot represent `24:00`
  anyway, so this loses no representable value.
- **Date control: style the native `<input type="date">`, not a custom
  calendar** — proportionate / YAGNI; consistent box with the selects. A custom
  react-day-picker dropdown (as in the public Booker) is a possible additive
  follow-up if 1-to-1 parity with the tz dropdown is later wanted. Chosen while
  the user was away; low-regret because it is the smaller option and the core
  asks don't depend on it.
- **Backend rejects off-grid times on save** — matches "проверять на бэке";
  the UI preserves legacy off-grid values on load but any edit snaps to whole
  hours.
