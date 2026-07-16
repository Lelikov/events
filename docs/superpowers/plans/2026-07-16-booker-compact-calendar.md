# Booker Compact Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Booker's tall vertical "all days' slots" picker with a compact cal.com-style month calendar (`react-day-picker`) where only bookable days are selectable and a slots column shows the chosen day's times.

**Architecture:** Frontend-only, `event-booker-frontend`. A new pure-helper module (`calendar.ts`) derives available days and month ranges from the existing `GET /slots` payload; `SlotPicker` is rewritten to render a three-region card (event info | `<DayPicker>` | slots for the selected day), fetching per displayed month and auto-selecting the first available day; `BookingFlowPage` passes event title/duration in and consumes the renamed slot callback; new local CSS themes react-day-picker to the design-system tokens and lays out the card responsively.

**Tech Stack:** React 19, Vite, TypeScript, `react-day-picker@^9`, vitest + happy-dom, plain global CSS with `events-design-system` tokens.

## Global Constraints

- **Package:** `event-booker-frontend` only. No backend/BFF/API/contract changes. `bookerApi.getSlots` keeps its signature; only caller window args change.
- **Repo/commits:** `event-booker-frontend` is **root-tracked** (no nested `.git`) → every commit in this plan is made from the repo root `/Users/alexandrlelikov/PycharmProjects/events`, staging `event-booker-frontend/...` paths, on branch `feat/booker-calendar`.
- **New dependency:** `react-day-picker@^9`. If its install docs require a peer date library, add exactly what they specify; add no other runtime deps.
- **Slots payload shape:** `Slots.slots` is `Record<"YYYY-MM-DD", string[]>` — keys are calendar dates, values are ISO datetime strings. An empty array or absent key = no availability that day.
- **Day-key rule:** map a `Date` to a slots key by its **local** calendar components: `` `${y}-${pad(m+1)}-${pad(d)}` `` (never `toISOString()`, which would shift across the UTC boundary).
- **Styling:** plain global CSS only (no CSS-in-JS). Theme react-day-picker via `react-day-picker/style.css` + `--rdp-*` variable overrides + `classNames`, mapped to design-system tokens (`--primary`, `--primary-pale`, `--muted`, `--text`, `--border`). Reuse `.slot-grid`/`.slot-button` for the slots column.
- **TS style:** function components, early returns/guard clauses, `import { X, type Y }` per the repo's `verbatimModuleSyntax`. `.ts`/`.tsx` extensions on relative imports (repo convention).
- **Locale:** Russian UI; use react-day-picker's `ru` locale (`import { ru } from 'react-day-picker/locale'`). 24-hour times (existing `formatTime`).
- **Tests:** vitest + happy-dom, `createRoot` + `act` pattern, mocking `./bookerApi.ts`'s `getSlots` (mirror the existing `SlotPicker.test.tsx`). Every task keeps `npm run build`, `npm test`, `npm run lint` green.

---

### Task 1: Pure calendar helpers + `react-day-picker` dependency

**Files:**
- Modify: `event-booker-frontend/package.json` (add `react-day-picker`)
- Create: `event-booker-frontend/src/modules/booking/calendar.ts`
- Modify: `event-booker-frontend/src/modules/booking/datetime.ts` (add `formatDayLabel`)
- Test: `event-booker-frontend/src/modules/booking/calendar.test.ts`

**Interfaces:**
- Produces (all pure, no React):
  - `dateKey(d: Date): string` — `"YYYY-MM-DD"` from local Y/M/D.
  - `startOfMonth(d: Date): Date` — local first-of-month at 00:00.
  - `startOfDay(d: Date): Date` — local midnight of `d`.
  - `parseDateKey(key: string): Date` — local Date from `"YYYY-MM-DD"`.
  - `monthRange(month: Date, now?: Date): { startISO: string; endISO: string }` — `[max(now, startOfMonth(month)) … startOfMonth(next month))` as ISO. `now` defaults to `new Date()`; it is a parameter only so tests can pin it.
  - `availableDaysFromSlots(slots: Slots): Set<string>` — keys whose array is non-empty.
  - `firstAvailableDay(slots: Slots): Date | null` — earliest key with slots, as a local Date, else `null`.
  - `datetime.ts`: `formatDayLabel(d: Date): string` — `"вт, 21 июля"` from the Date's **local** calendar components (no `timeZone` option).

- [ ] **Step 1: Add the dependency**

Edit `event-booker-frontend/package.json` — add to `dependencies`: `"react-day-picker": "^9.4.0"`. Then:

Run: `cd event-booker-frontend && npm install`
Expected: installs `react-day-picker` (and its bundled date utilities). If npm reports a required peer (e.g. `date-fns`), install exactly that: `npm install date-fns` — nothing else.

- [ ] **Step 2: Write the failing test `calendar.test.ts`**

```ts
import { describe, expect, it } from 'vitest'
import { availableDaysFromSlots, dateKey, firstAvailableDay, monthRange, parseDateKey, startOfMonth } from './calendar.ts'
import type { Slots } from './types.ts'

const slots = (map: Record<string, string[]>): Slots => ({ event_type_id: 'e1', time_zone: 'UTC', slots: map })

describe('calendar helpers', () => {
  it('dateKey uses local calendar components', () => {
    expect(dateKey(new Date(2026, 9, 1, 23, 30))).toBe('2026-10-01') // month is 0-based → October
    expect(dateKey(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('parseDateKey round-trips with dateKey', () => {
    expect(dateKey(parseDateKey('2026-07-21'))).toBe('2026-07-21')
  })

  it('availableDaysFromSlots keeps only non-empty days', () => {
    const s = availableDaysFromSlots(slots({ '2026-07-21': ['x'], '2026-07-22': [], '2026-07-23': ['y', 'z'] }))
    expect([...s].sort()).toEqual(['2026-07-21', '2026-07-23'])
  })

  it('firstAvailableDay returns the earliest day with slots, or null', () => {
    expect(dateKey(firstAvailableDay(slots({ '2026-07-23': ['y'], '2026-07-21': ['x'] }))!)).toBe('2026-07-21')
    expect(firstAvailableDay(slots({ '2026-07-22': [] }))).toBeNull()
    expect(firstAvailableDay(slots({}))).toBeNull()
  })

  it('monthRange spans one month and clamps the start to now', () => {
    const r = monthRange(new Date(2030, 0, 10), new Date(2029, 0, 1)) // now far before → start = month start
    const days = (new Date(r.endISO).getTime() - new Date(r.startISO).getTime()) / 86_400_000
    expect(days).toBeGreaterThanOrEqual(28)
    expect(days).toBeLessThanOrEqual(31)
    const clamped = monthRange(new Date(2030, 0, 1), new Date(2030, 0, 20)) // now inside month → start = now
    expect(new Date(clamped.startISO).getTime()).toBe(new Date(2030, 0, 20).getTime())
  })
})
```

- [ ] **Step 3: Run it — expect failure**

Run: `cd event-booker-frontend && npm test -- calendar.test.ts`
Expected: FAIL (module `./calendar.ts` not found).

- [ ] **Step 4: Implement `calendar.ts`**

```ts
import type { Slots } from './types.ts'

const pad = (n: number) => String(n).padStart(2, '0')

export function dateKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

export function parseDateKey(key: string): Date {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function monthRange(month: Date, now: Date = new Date()): { startISO: string; endISO: string } {
  const first = startOfMonth(month)
  const start = now.getTime() > first.getTime() ? now : first
  const end = new Date(month.getFullYear(), month.getMonth() + 1, 1)
  return { startISO: start.toISOString(), endISO: end.toISOString() }
}

export function availableDaysFromSlots(slots: Slots): Set<string> {
  return new Set(Object.keys(slots.slots).filter((k) => slots.slots[k].length > 0))
}

export function firstAvailableDay(slots: Slots): Date | null {
  const keys = Object.keys(slots.slots)
    .filter((k) => slots.slots[k].length > 0)
    .sort()
  return keys.length > 0 ? parseDateKey(keys[0]) : null
}
```

- [ ] **Step 5: Add `formatDayLabel` to `datetime.ts`**

Append:

```ts
export function formatDayLabel(d: Date): string {
  return new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }).format(d)
}
```

- [ ] **Step 6: Run tests + build + lint**

Run: `cd event-booker-frontend && npm test -- calendar.test.ts && npm run build && npm run lint`
Expected: calendar tests pass; build + lint clean.

- [ ] **Step 7: Commit (from repo root)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/package.json event-booker-frontend/package-lock.json \
        event-booker-frontend/src/modules/booking/calendar.ts \
        event-booker-frontend/src/modules/booking/calendar.test.ts \
        event-booker-frontend/src/modules/booking/datetime.ts
git commit -m "feat(booker): calendar helpers + react-day-picker dependency"
```

---

### Task 2: Rewrite `SlotPicker` as the calendar + slots step, and rewire `BookingFlowPage`

Both change together because `SlotPicker`'s prop shape changes and `BookingFlowPage` is its only caller — doing them in one task keeps the build green.

**Files:**
- Modify (rewrite): `event-booker-frontend/src/modules/booking/SlotPicker.tsx`
- Modify: `event-booker-frontend/src/modules/booking/BookingFlowPage.tsx`
- Test (rewrite): `event-booker-frontend/src/modules/booking/SlotPicker.test.tsx`
- Check (keep green): `event-booker-frontend/src/modules/booking/BookingFlowPage.test.tsx`

**Interfaces:**
- Consumes: `calendar.ts` helpers + `formatDayLabel` (Task 1); `getSlots` (`bookerApi.ts`); `formatTime` (`datetime.ts`); `Slots` (`types.ts`).
- Produces — new `SlotPicker` props:
  ```ts
  type Props = {
    eventTypeId: string
    eventTitle: string
    durationMinutes: number
    timeZone: string
    onTimeZoneChange: (tz: string) => void
    onSelectSlot: (startTime: string) => void
    initialMonth?: Date            // testability + initial displayed month; defaults to startOfMonth(new Date())
  }
  ```

- [ ] **Step 1: Rewrite the test `SlotPicker.test.tsx`**

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SlotPicker } from './SlotPicker.tsx'
import { dateKey, startOfMonth } from './calendar.ts'
import type { Slots } from './types.ts'

vi.mock('./bookerApi.ts', () => ({ getSlots: vi.fn() }))
import { getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

function futureDay(offset: number): Date {
  const t = new Date()
  return new Date(t.getFullYear(), t.getMonth(), t.getDate() + offset)
}

async function mount(slots: Slots, onSelectSlot = vi.fn(), initialMonth?: Date) {
  vi.mocked(getSlots).mockResolvedValue(slots)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <SlotPicker
        eventTypeId="e1"
        eventTitle="Тест"
        durationMinutes={30}
        timeZone="UTC"
        onTimeZoneChange={vi.fn()}
        onSelectSlot={onSelectSlot}
        initialMonth={initialMonth}
      />,
    )
  })
  await act(async () => {})
  return { onSelectSlot }
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

const slots = (map: Record<string, string[]>): Slots => ({ event_type_id: 'e1', time_zone: 'UTC', slots: map })

describe('SlotPicker (calendar)', () => {
  it('auto-selects the first available day and lists its slots', async () => {
    const day = futureDay(2)
    const key = dateKey(day)
    const iso = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9, 0).toISOString()
    const { onSelectSlot } = await mount(slots({ [key]: [iso] }), vi.fn(), startOfMonth(day))
    const buttons = container.querySelectorAll('.slot-button')
    expect(buttons.length).toBe(1)
    await act(async () => (buttons[0] as HTMLButtonElement).click())
    expect(onSelectSlot).toHaveBeenCalledWith(iso)
  })

  it('shows an empty message when the month has no slots', async () => {
    await mount(slots({}))
    expect(container.textContent).toContain('Нет свободных слотов')
    expect(container.querySelectorAll('.slot-button').length).toBe(0)
  })

  it('refetches when the month is changed', async () => {
    const day = futureDay(2)
    await mount(slots({ [dateKey(day)]: [new Date().toISOString()] }), vi.fn(), startOfMonth(day))
    expect(vi.mocked(getSlots)).toHaveBeenCalledTimes(1)
    const next = container.querySelector('.rdp-nav')!.querySelectorAll('button')
    await act(async () => (next[next.length - 1] as HTMLButtonElement).click())
    expect(vi.mocked(getSlots).mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})
```

> Note on the month-nav selector: react-day-picker v9 renders its previous/next arrows inside a `.rdp-nav` container. If the installed version emits a different wrapper, read the rendered DOM (`container.innerHTML`) once and adjust the selector — the assertion (a second `getSlots` call after clicking the next-month arrow) stays the same.

- [ ] **Step 2: Run the tests — expect failure**

Run: `cd event-booker-frontend && npm test -- SlotPicker.test.tsx`
Expected: FAIL (new props / calendar markup not present).

- [ ] **Step 3: Rewrite `SlotPicker.tsx`**

```tsx
import { useEffect, useMemo, useState } from 'react'
import { DayPicker } from 'react-day-picker'
import { ru } from 'react-day-picker/locale'
import 'react-day-picker/style.css'
import { getSlots } from './bookerApi.ts'
import { formatDayLabel, formatTime } from './datetime.ts'
import { availableDaysFromSlots, dateKey, firstAvailableDay, monthRange, startOfDay, startOfMonth } from './calendar.ts'
import type { Slots } from './types.ts'

const COMMON_ZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'UTC']

type Props = {
  eventTypeId: string
  eventTitle: string
  durationMinutes: number
  timeZone: string
  onTimeZoneChange: (tz: string) => void
  onSelectSlot: (startTime: string) => void
  initialMonth?: Date
}

type FetchResult = { requestId: string; slots: Slots | null; error: boolean }

export function SlotPicker({
  eventTypeId,
  eventTitle,
  durationMinutes,
  timeZone,
  onTimeZoneChange,
  onSelectSlot,
  initialMonth,
}: Props) {
  const [month, setMonth] = useState<Date>(() => initialMonth ?? startOfMonth(new Date()))
  const [clickedDay, setClickedDay] = useState<Date | null>(null)
  const [result, setResult] = useState<FetchResult>({ requestId: '', slots: null, error: false })

  const { startISO, endISO } = useMemo(() => monthRange(month), [month])
  const requestId = `${eventTypeId}|${timeZone}|${startISO}`

  useEffect(() => {
    let active = true
    getSlots(eventTypeId, startISO, endISO, timeZone)
      .then((s) => active && setResult({ requestId, slots: s, error: false }))
      .catch(() => active && setResult({ requestId, slots: null, error: true }))
    return () => {
      active = false
    }
  }, [eventTypeId, timeZone, startISO, endISO, requestId])

  const isCurrent = result.requestId === requestId
  const slots = isCurrent ? result.slots : null
  const error = isCurrent && result.error

  const availableDays = useMemo(() => (slots ? availableDaysFromSlots(slots) : new Set<string>()), [slots])

  // Effective selection: the user's clicked day if still available, else the first available day.
  const selectedDay = useMemo(() => {
    if (!slots) return null
    if (clickedDay && availableDays.has(dateKey(clickedDay))) return clickedDay
    return firstAvailableDay(slots)
  }, [slots, clickedDay, availableDays])

  const today = startOfDay(new Date())
  const daySlots = slots && selectedDay ? (slots.slots[dateKey(selectedDay)] ?? []) : []
  const zones = COMMON_ZONES.includes(timeZone) ? COMMON_ZONES : [timeZone, ...COMMON_ZONES]

  return (
    <div className="cal-card">
      <div className="cal-info">
        <h2>{eventTitle}</h2>
        <p className="muted">{durationMinutes} мин</p>
        <label className="field">
          <span>Часовой пояс</span>
          <select value={timeZone} onChange={(e) => onTimeZoneChange(e.target.value)}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="cal-grid">
        <DayPicker
          mode="single"
          locale={ru}
          month={month}
          onMonthChange={setMonth}
          startMonth={startOfMonth(new Date())}
          selected={selectedDay ?? undefined}
          onSelect={(d) => d && setClickedDay(d)}
          disabled={(d) => d < today || !availableDays.has(dateKey(d))}
          modifiers={{ available: (d) => availableDays.has(dateKey(d)) }}
          modifiersClassNames={{ available: 'rdp-available' }}
        />
      </div>

      <div className="cal-slots">
        {error && <p className="banner-error">Не удалось загрузить слоты. Попробуйте ещё раз.</p>}
        {!error && slots === null && <p className="muted">Загрузка…</p>}
        {!error && slots !== null && selectedDay === null && <p className="muted">Нет свободных слотов</p>}
        {!error && selectedDay !== null && (
          <>
            <h3 className="cal-day-header">{formatDayLabel(selectedDay)}</h3>
            {daySlots.length === 0 ? (
              <p className="muted">Нет свободных слотов</p>
            ) : (
              <div className="slot-grid">
                {daySlots.map((iso) => (
                  <button key={iso} type="button" className="slot-button" onClick={() => onSelectSlot(iso)}>
                    {formatTime(iso, timeZone)}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Rewire `BookingFlowPage.tsx`** — pass the event title/duration and use the renamed callback. Replace the `<SlotPicker … />` block in the `step === 'slot'` branch with:

```tsx
{step === 'slot' && eventType && (
  <SlotPicker
    eventTypeId={eventTypeId}
    eventTitle={eventType.title}
    durationMinutes={eventType.duration_minutes}
    timeZone={timeZone}
    onTimeZoneChange={setTimeZone}
    onSelectSlot={handleSelect}
  />
)}
```

Leave `handleSelect`, the header `<h1>`/duration line, and the rest of `BookingFlowPage` unchanged. (The step only renders once `eventType` has loaded; before that the header already shows "Бронирование".)

- [ ] **Step 5: Run the Booker suite — expect green**

Run: `cd event-booker-frontend && npm test`
Expected: `SlotPicker.test.tsx` passes; `BookingFlowPage.test.tsx` still passes. If `BookingFlowPage.test.tsx` asserted on old SlotPicker markup or the `onSelect` prop name, update it to the new prop shape (it drives the flow via the rendered slot buttons / `onSelectSlot`), keeping its original intent.

- [ ] **Step 6: Build + lint**

Run: `cd event-booker-frontend && npm run build && npm run lint`
Expected: clean. (The `selectedDay` `useMemo` has all deps listed — no `react-hooks/exhaustive-deps` warning.)

- [ ] **Step 7: Commit (from repo root)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/SlotPicker.tsx \
        event-booker-frontend/src/modules/booking/SlotPicker.test.tsx \
        event-booker-frontend/src/modules/booking/BookingFlowPage.tsx \
        event-booker-frontend/src/modules/booking/BookingFlowPage.test.tsx
git commit -m "feat(booker): compact react-day-picker calendar with per-month availability"
```

---

### Task 3: Theme react-day-picker + three-region layout (CSS)

CSS has no unit test; its deliverable is a correct build + the intended compact layout. Fold it into its own task because it is a distinct, reviewable chunk.

**Files:**
- Modify: `event-booker-frontend/src/App.css`

**Interfaces:**
- Consumes: the class names emitted by `SlotPicker` (`.cal-card`, `.cal-info`, `.cal-grid`, `.cal-slots`, `.cal-day-header`, plus the `.rdp-available` modifier class) and react-day-picker's own `.rdp-*` structure; the design-system tokens from `events-design-system/styles.css` (already imported by the Booker).

- [ ] **Step 1: Add the calendar CSS to `event-booker-frontend/src/App.css`**

```css
/* ── Compact calendar step ─────────────────────────────────── */
.cal-card {
  display: grid;
  grid-template-columns: 200px 1fr auto;
  gap: 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  box-shadow: var(--shadow);
  padding: 20px;
  margin-top: 16px;
}
.cal-info { display: grid; gap: 10px; align-content: start; }
.cal-info h2 { font-size: 18px; }
.cal-grid { display: flex; justify-content: center; }
.cal-slots { min-width: 150px; display: grid; gap: 10px; align-content: start; }
.cal-day-header { font-size: 13px; font-weight: 600; color: var(--text-2); }
.cal-slots .slot-grid { flex-direction: column; flex-wrap: nowrap; max-height: 320px; overflow-y: auto; }

/* react-day-picker → design-system tokens */
.rdp-root {
  --rdp-accent-color: var(--primary);
  --rdp-accent-background-color: var(--primary-pale);
  --rdp-today-color: var(--primary);
  --rdp-day-width: 38px;
  --rdp-day-height: 38px;
  --rdp-day_button-width: 38px;
  --rdp-day_button-height: 38px;
  font-size: 13px;
}
.rdp-day_button { border-radius: 8px; color: var(--text); }
.rdp-selected .rdp-day_button { background: var(--primary); color: #fff; }
.rdp-disabled { color: var(--muted); opacity: 0.45; }
.rdp-available:not(.rdp-selected) .rdp-day_button { background: var(--primary-pale); color: var(--primary-dark); }
.rdp-chevron { fill: var(--muted); }

@media (max-width: 720px) {
  .cal-card { grid-template-columns: 1fr; }
  .cal-slots .slot-grid { flex-direction: row; flex-wrap: wrap; max-height: none; }
}
```

> The `--rdp-*` variable names and part class names (`.rdp-day_button`, `.rdp-selected`, `.rdp-disabled`, `.rdp-chevron`, `.rdp-nav`) are v9's. After building, open the Booker and confirm the mapping visually; if the installed v9 minor renames a part, adjust that selector only (the token values stay).

- [ ] **Step 2: Build + full suite + lint**

Run: `cd event-booker-frontend && npm run build && npm test && npm run lint`
Expected: all green (CSS doesn't change test outcomes).

- [ ] **Step 3: Manual visual smoke**

Run: `cd event-booker-frontend && npm run dev` — open a booking flow (event-type → slot step). Confirm: a single compact card with `[info | month grid | slots]`; only available days are clickable and tinted, past/empty days dimmed; the first available day is pre-selected and its times show on the right; month arrows page and reload availability; at a narrow width the three regions stack.

- [ ] **Step 4: Commit (from repo root)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/App.css
git commit -m "style(booker): theme react-day-picker + compact three-region calendar layout"
```

---

## Notes for the executor

- **Branch:** create `feat/booker-calendar` in the root `events` repo before Task 1. It builds on the current Booker state (which already consumes `events-design-system`), so branch off the current design-system branch tip, not `main`.
- **Task order:** 1 (helpers/dep) → 2 (component + caller, one task to stay green) → 3 (CSS). Task 2 depends on Task 1's helpers; Task 3 depends on Task 2's class names.
- **react-day-picker v9 specifics** the tasks assume: `import { DayPicker } from 'react-day-picker'`, `import { ru } from 'react-day-picker/locale'`, `import 'react-day-picker/style.css'`; props `mode`, `month`, `onMonthChange`, `startMonth`, `selected`, `onSelect`, `disabled` (matcher fn), `modifiers`, `modifiersClassNames`. If `npm install` pulls a v9 minor whose CSS path or a part class differs, adjust the import/selector to the installed package and note it — do not change the behaviour or the token values.
- **Determinism:** tests pin the displayed month via `initialMonth` and place slots on `today + 2 days`, so they pass regardless of when they run; keep that pattern for any new test.
