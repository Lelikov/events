# Schedule Editor: Whole-Hour Time Select + Picker Styling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the organizer cabinet schedule editor, replace bare native time/date inputs with design-system-styled controls (time becomes a whole-hour `<select>`) and enforce the whole-hour invariant on the `event-scheduling` save path.

**Architecture:** Frontend (`event-organizer-frontend`) gains a small `HourSelect` component (native `<select>` of `00:00…23:00`) that replaces the four `<input type="time">` in `WeeklyHours`/`DateOverrides`; date inputs and the hour select share a new `.field-control` CSS box matching the design-system selects. Backend (`event-scheduling`) adds a whole-hour check to the two existing schedule validators, which already run on `PUT /api/v1/schedules/{owner}`.

**Tech Stack:** React 19 + Vite + TS, plain CSS, vitest + happy-dom (`createRoot`+`act`, no testing-library); Python 3.14 FastAPI, pytest.

## Global Constraints

- No `else if`; avoid `else` — early returns / guard clauses / mappings (both codebases).
- Frontend: plain CSS only, UI copy Russian, no router lib, design-system tokens (`var(--bg-soft)`, `var(--border)`, `var(--radius-sm)`, `var(--primary)`, `var(--text)`).
- Whole hours mean `00:00`–`23:00`; values are `HH:00` strings; the domain `time` has `minute==second==microsecond==0`.
- Frontend tests: `createRoot` + `act`, no testing-library; drive `<select>` via the native value setter + a dispatched `input`/`change` event.
- Backend: `ValidationError` maps to HTTP 422; times are `datetime.time`.
- Schedule DTO/contract shape is unchanged — no new fields, no new endpoints.

---

### Task 1: Backend whole-hour validation

**Files:**
- Modify: `event-scheduling/event_scheduling/validation.py`
- Test: `event-scheduling/tests/test_validation.py`

**Interfaces:**
- Consumes: `WeeklyHourDTO(day_of_week: int, start_time: time, end_time: time)`, `DateOverrideDTO(date, start_time: time | None, end_time: time | None)`, `ValidationError` (all already imported in the test module).
- Produces: no new public symbols; `validate_weekly_hours` and `validate_date_overrides` gain a whole-hour rule. A private `_is_whole_hour(t: time) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `event-scheduling/tests/test_validation.py`:

```python
def test_weekly_hours_rejects_non_whole_hour() -> None:
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(1, dt.time(9, 30), dt.time(17))])
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(1, dt.time(9), dt.time(17, 15))])
    validate_weekly_hours([WeeklyHourDTO(1, dt.time(9), dt.time(17))])  # whole hours ok


def test_date_override_rejects_non_whole_hour() -> None:
    with pytest.raises(ValidationError):
        validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9, 30), dt.time(12))])
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9), dt.time(12))])  # ok
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), None, None)])  # full-day ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd event-scheduling && uv run pytest tests/test_validation.py::test_weekly_hours_rejects_non_whole_hour tests/test_validation.py::test_date_override_rejects_non_whole_hour -v`
Expected: FAIL — the `:30`/`:15` inputs currently pass validation (no error raised).

- [ ] **Step 3: Implement the whole-hour rule**

In `event-scheduling/event_scheduling/validation.py`, add the helper after the imports/constants (before `validate_time_zone`):

```python
from datetime import time


def _is_whole_hour(t: time) -> bool:
    return t.minute == 0 and t.second == 0 and t.microsecond == 0
```

(Note: `time` is imported here explicitly — the module currently imports only `Sequence`/`zoneinfo`; add `from datetime import time`.)

Extend `validate_weekly_hours` — inside the existing `for r in rows:` loop, after the `end_time <= start_time` check add:

```python
        if not _is_whole_hour(r.start_time) or not _is_whole_hour(r.end_time):
            raise ValidationError(f"weekly_hours times must be on the hour (day {r.day_of_week})")
```

Extend `validate_date_overrides` — inside the existing `for r in rows:` loop, in the `both_set` path (after the `end_time <= start_time` check), add:

```python
        if both_set and (not _is_whole_hour(r.start_time) or not _is_whole_hour(r.end_time)):
            raise ValidationError(f"date_override {r.date}: times must be on the hour")
```

(`both_set` guarantees `start_time`/`end_time` are not `None`, so `_is_whole_hour` never sees `None`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd event-scheduling && uv run pytest tests/test_validation.py -v`
Expected: PASS — all validation tests, including the two new ones and the pre-existing `test_weekly_hours_rejects_bad_day_and_range` / `test_date_override_null_invariant`.

- [ ] **Step 5: Lint**

Run: `cd event-scheduling && uv run ruff check event_scheduling/validation.py && uv run ruff format event_scheduling/validation.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add event-scheduling/event_scheduling/validation.py event-scheduling/tests/test_validation.py
git commit -m "feat(scheduling): reject non-whole-hour schedule times on save"
```

---

### Task 2: HourSelect component

**Files:**
- Create: `event-organizer-frontend/src/modules/schedule/HourSelect.tsx`
- Test: `event-organizer-frontend/src/modules/schedule/HourSelect.test.tsx`

**Interfaces:**
- Produces: `export function HourSelect(props: { value: string; onChange: (v: string) => void; ariaLabel?: string }): JSX.Element` — renders `<select className="field-control field-control--select">`. Options are the 24 strings `"00:00".."23:00"`. If `value` is a non-empty string not among those 24, it is prepended as an extra `<option>` (legacy off-grid safety).
- Produces: `export const HOUR_OPTIONS: string[]` — the 24 whole-hour strings (exported so tests and callers can reference the canonical list).

- [ ] **Step 1: Write the failing test**

Create `event-organizer-frontend/src/modules/schedule/HourSelect.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { HourSelect, HOUR_OPTIONS } from './HourSelect.tsx'

let container: HTMLDivElement
let root: Root

async function mount(value: string, onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<HourSelect value={value} onChange={onChange} ariaLabel="Начало" />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

function selectValue(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

describe('HourSelect', () => {
  it('exposes 24 whole-hour options from 00:00 to 23:00', () => {
    expect(HOUR_OPTIONS).toHaveLength(24)
    expect(HOUR_OPTIONS[0]).toBe('00:00')
    expect(HOUR_OPTIONS[9]).toBe('09:00')
    expect(HOUR_OPTIONS[23]).toBe('23:00')
  })

  it('renders a select of 24 whole-hour options for a whole-hour value', async () => {
    await mount('09:00')
    const select = container.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('09:00')
    expect(select.querySelectorAll('option')).toHaveLength(24)
  })

  it('preserves a legacy off-grid value as an extra selected option', async () => {
    await mount('09:30')
    const select = container.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('09:30')
    expect(select.querySelectorAll('option')).toHaveLength(25)
  })

  it('fires onChange with the picked whole-hour value', async () => {
    const onChange = await mount('09:00')
    const select = container.querySelector('select') as HTMLSelectElement
    await act(async () => selectValue(select, '14:00'))
    expect(onChange).toHaveBeenCalledWith('14:00')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/schedule/HourSelect.test.tsx`
Expected: FAIL — `HourSelect.tsx` does not exist (import error).

- [ ] **Step 3: Implement HourSelect**

Create `event-organizer-frontend/src/modules/schedule/HourSelect.tsx`:

```tsx
export const HOUR_OPTIONS: string[] = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`)

type Props = {
  value: string
  onChange: (v: string) => void
  ariaLabel?: string
}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd event-organizer-frontend && npx vitest run src/modules/schedule/HourSelect.test.tsx`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add event-organizer-frontend/src/modules/schedule/HourSelect.tsx event-organizer-frontend/src/modules/schedule/HourSelect.test.tsx
git commit -m "feat(organizer-fe): add whole-hour HourSelect component"
```

---

### Task 3: Wire HourSelect into the editor + style date/select controls

**Files:**
- Modify: `event-organizer-frontend/src/modules/schedule/WeeklyHours.tsx`
- Modify: `event-organizer-frontend/src/modules/schedule/DateOverrides.tsx`
- Modify: `event-organizer-frontend/src/modules/schedule/Travel.tsx`
- Modify: `event-organizer-frontend/src/index.css`
- Test: `event-organizer-frontend/src/modules/schedule/WeeklyHours.test.tsx` (add one interaction test)

**Interfaces:**
- Consumes: `HourSelect` from `./HourSelect.tsx` (Task 2).

- [ ] **Step 1: Replace the time inputs in WeeklyHours**

In `WeeklyHours.tsx`, add to the imports:

```tsx
import { HourSelect } from './HourSelect.tsx'
```

Replace the two `<input type="time">` blocks (start and end) inside the `interval-row` with:

```tsx
                    <HourSelect
                      value={iv.start}
                      ariaLabel="Начало"
                      onChange={(v) => setTime(idx, ivIdx, 'start', v)}
                    />
                    <span>–</span>
                    <HourSelect
                      value={iv.end}
                      ariaLabel="Конец"
                      onChange={(v) => setTime(idx, ivIdx, 'end', v)}
                    />
```

Leave `setTime`, the `key={iv.uid}` row, and the remove button unchanged.

- [ ] **Step 2: Replace the time inputs in DateOverrides**

In `DateOverrides.tsx`, add to the imports:

```tsx
import { HourSelect } from './HourSelect.tsx'
```

Replace the two `<input type="time">` in the `!o.fullDay` fragment with:

```tsx
              <HourSelect value={o.start} ariaLabel="Начало" onChange={(v) => update(idx, { ...o, start: v })} />
              <span>–</span>
              <HourSelect value={o.end} ariaLabel="Конец" onChange={(v) => update(idx, { ...o, end: v })} />
```

Add the `field-control` class to the date input on the same component:

```tsx
        <input
          type="date"
          className="field-control"
          value={o.date}
          onChange={(e) => update(idx, { ...o, date: e.target.value })}
        />
```

- [ ] **Step 3: Style the date inputs in Travel**

In `Travel.tsx`, add `className="field-control"` to both date inputs (start_date and end_date), leaving everything else unchanged:

```tsx
        <input
          type="date"
          className="field-control"
          value={t.start_date}
          onChange={(e) => update(idx, { ...t, start_date: e.target.value })}
        />
        <span>–</span>
        <input
          type="date"
          className="field-control"
          value={t.end_date}
          onChange={(e) => update(idx, { ...t, end_date: e.target.value })}
        />
```

- [ ] **Step 4: Add the shared control CSS**

In `event-organizer-frontend/src/index.css`, remove the now-dead rule
`.interval-row input[type="time"] { width: 120px; }` (line 46) and add:

```css
/* Row-level controls (hour select, date input) reuse the design-system field
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
.field-control--select {
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236b7280'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 30px;
  cursor: pointer;
}
```

- [ ] **Step 5: Add a WeeklyHours interaction test for the new select**

Append to the `describe('WeeklyHours', …)` block in `WeeklyHours.test.tsx`:

```tsx
  it('changing the start hour updates the interval via HourSelect', async () => {
    const days = emptyDays()
    days[0] = { enabled: true, intervals: [{ uid: 'u1', start: '09:00', end: '12:00' }] }
    const onChange = await mount(days)
    const select = container.querySelector('.weekday-row select') as HTMLSelectElement
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(select, '10:00')
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const next = onChange.mock.calls[0][0] as DayState[]
    expect(next[0].intervals[0]).toEqual({ uid: 'u1', start: '10:00', end: '12:00' })
  })
```

- [ ] **Step 6: Run the full frontend suite + typecheck**

Run: `cd event-organizer-frontend && npx vitest run && npm run build`
Expected: all tests PASS (existing + `HourSelect` + the new WeeklyHours case), `tsc` clean, build ok.

- [ ] **Step 7: Commit**

```bash
git add event-organizer-frontend/src/modules/schedule/WeeklyHours.tsx event-organizer-frontend/src/modules/schedule/DateOverrides.tsx event-organizer-frontend/src/modules/schedule/Travel.tsx event-organizer-frontend/src/index.css event-organizer-frontend/src/modules/schedule/WeeklyHours.test.tsx
git commit -m "feat(organizer-fe): hour-select + styled date controls in schedule editor"
```

---

### Task 4: Docs

**Files:**
- Modify: `event-organizer-frontend/CLAUDE.md`
- Modify: `event-scheduling/docs/API_CONTRACTS.md`

- [ ] **Step 1: Note the whole-hour rule in the organizer-fe CLAUDE.md**

In `event-organizer-frontend/CLAUDE.md`, under the `schedule/` screen description, add a sentence: the weekly-hours and date-override times are chosen with a whole-hour `<select>` (`HourSelect`); the domain enforces whole hours on save.

- [ ] **Step 2: Note the whole-hour invariant in event-scheduling API contracts**

In `event-scheduling/docs/API_CONTRACTS.md`, under `PUT /api/v1/schedules/{owner_user_id}`, add that `weekly_hours` and `date_override` start/end times must be on the hour (minute/second == 0), else `422`.

- [ ] **Step 3: Commit**

```bash
git add event-organizer-frontend/CLAUDE.md event-scheduling/docs/API_CONTRACTS.md
git commit -m "docs: whole-hour schedule times (organizer-fe + scheduling API)"
```

---

## Notes for the executor

- `event-organizer-frontend` and the root docs are tracked by the **root** `events` git repo; `event-scheduling` is its **own** nested git repo. Commit each in its own repo — run the `event-scheduling` commit from inside `event-scheduling/`, and never `git add -A` at the root (it sweeps in embedded-repo gitlinks).
- The existing `WeeklyHours`/`DateOverrides`/`SchedulePage` tests do not query `input[type="time"]`, so swapping to `<select>` does not break them; only the new interaction test drives the select.
- Backend tests need PostgreSQL only for DB-touching suites; `test_validation.py` is pure and runs without a DB.
