# Schedule Page: Single Save + Unsaved Highlight + Leave Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the schedule page's two save buttons with one that persists only the dirty endpoint(s), highlight unsaved sections, and warn before leaving with unsaved changes.

**Architecture:** A pure `computeDirty(current, saved)` helper drives per-section markers and a single Save button in `SchedulePage`. A tiny `navGuard` module lets `navigateTo` (all in-app nav) prompt via `window.confirm` when a registered blocker reports unsaved changes; `beforeunload` covers close/refresh. Save writes `putSchedule` and/or `putTravel` depending on which slice changed, then advances the saved baseline.

**Tech Stack:** React 19 + Vite + TS, plain CSS, vitest + happy-dom (`createRoot`+`act`, no testing-library).

## Global Constraints

- No `else if`; avoid `else` — early returns / guard clauses / mappings.
- Plain CSS only; UI copy Russian; design-system tokens (`var(--primary)`, `var(--border)`, etc.).
- Endpoints unchanged: `putSchedule(buildUpsert(state))`, `putTravel(buildTravel(state))`.
- Save only the dirty slice(s) to avoid a spurious `schedule_change_log` snapshot.
- Guard skips same-path navigations and any `navigateTo(..., { skipGuard: true })`.
- Warning UI is native: `window.confirm` for in-app nav/logout, `beforeunload` for close/refresh. Confirm copy: `Есть несохранённые изменения. Уйти без сохранения?`.
- Tests: `createRoot` + `act`, no testing-library; mock `window.confirm` and `apiRequest`/api functions.

---

### Task 1: `computeDirty` pure helper

**Files:**
- Modify: `event-organizer-frontend/src/modules/schedule/schedule.ts`
- Test: `event-organizer-frontend/src/modules/schedule/schedule.test.ts`

**Interfaces:**
- Produces: `export type DirtyFlags = { tz: boolean; weekly: boolean; overrides: boolean; travel: boolean; schedule: boolean; any: boolean }` and `export function computeDirty(current: EditorState, saved: EditorState): DirtyFlags`.

- [ ] **Step 1: Write the failing tests**

Append to `schedule.test.ts` (it already imports from `./schedule.ts`; add `computeDirty` and `bundleToState` to that import, and `emptyDays` if not present):

```ts
describe('computeDirty', () => {
  const base = () => bundleToState(null, 'Europe/Moscow') // {name, tz, days, overrides:[], travels:[]}

  it('reports nothing dirty for an identical snapshot', () => {
    const s = base()
    const d = computeDirty(s, s)
    expect(d).toEqual({ tz: false, weekly: false, overrides: false, travel: false, schedule: false, any: false })
  })

  it('flags a time-zone change as schedule-dirty', () => {
    const saved = base()
    const cur = { ...saved, timeZone: 'Europe/Berlin' }
    const d = computeDirty(cur, saved)
    expect(d.tz).toBe(true)
    expect(d.schedule).toBe(true)
    expect(d.any).toBe(true)
    expect(d.travel).toBe(false)
  })

  it('flags a weekly-hours change', () => {
    const saved = base()
    const cur = { ...saved, days: saved.days.map((d, i) => (i === 0 ? { enabled: true, intervals: [{ uid: 'x', start: '09:00', end: '10:00' }] } : d)) }
    const d = computeDirty(cur, saved)
    expect(d.weekly).toBe(true)
    expect(d.schedule).toBe(true)
  })

  it('flags an overrides change', () => {
    const saved = base()
    const cur = { ...saved, overrides: [{ uid: 'o', date: '2026-08-01', fullDay: true, start: '', end: '' }] }
    const d = computeDirty(cur, saved)
    expect(d.overrides).toBe(true)
    expect(d.schedule).toBe(true)
  })

  it('flags a travel change without marking the schedule part dirty', () => {
    const saved = base()
    const cur = { ...saved, travels: [{ uid: 't', start_date: '2026-08-01', end_date: '', time_zone: 'Asia/Dubai' }] }
    const d = computeDirty(cur, saved)
    expect(d.travel).toBe(true)
    expect(d.schedule).toBe(false)
    expect(d.any).toBe(true)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/schedule/schedule.test.ts`
Expected: FAIL — `computeDirty` is not exported.

- [ ] **Step 3: Implement `computeDirty`**

Add to `schedule.ts` (after the `EditorState` type or near the other exports):

```ts
export type DirtyFlags = { tz: boolean; weekly: boolean; overrides: boolean; travel: boolean; schedule: boolean; any: boolean }

export function computeDirty(current: EditorState, saved: EditorState): DirtyFlags {
  const tz = current.timeZone !== saved.timeZone
  const weekly = JSON.stringify(current.days) !== JSON.stringify(saved.days)
  const overrides = JSON.stringify(current.overrides) !== JSON.stringify(saved.overrides)
  const travel = JSON.stringify(current.travels) !== JSON.stringify(saved.travels)
  const schedule = tz || weekly || overrides
  return { tz, weekly, overrides, travel, schedule, any: schedule || travel }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd event-organizer-frontend && npx vitest run src/modules/schedule/schedule.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add event-organizer-frontend/src/modules/schedule/schedule.ts event-organizer-frontend/src/modules/schedule/schedule.test.ts
git commit -m "feat(organizer-fe): computeDirty helper for schedule editor"
```

---

### Task 2: `navGuard` module + `navigateTo` guard

**Files:**
- Create: `event-organizer-frontend/src/modules/shared/navGuard.ts`
- Create: `event-organizer-frontend/src/modules/shared/navGuard.test.ts`
- Modify: `event-organizer-frontend/src/modules/shared/routing.ts`
- Test: `event-organizer-frontend/src/modules/shared/routing.test.ts`

**Interfaces:**
- Produces: `setNavBlocker(fn: (() => boolean) | null): void`, `confirmLeaveIfBlocked(): boolean` (both in `navGuard.ts`).
- Modifies: `navigateTo(path: string, options?: { replace?: boolean; skipGuard?: boolean }): void`.

- [ ] **Step 1: Write the failing navGuard test**

Create `event-organizer-frontend/src/modules/shared/navGuard.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmLeaveIfBlocked, setNavBlocker } from './navGuard.ts'

afterEach(() => {
  setNavBlocker(null)
  vi.restoreAllMocks()
})

describe('navGuard', () => {
  it('allows navigation when no blocker is registered', () => {
    const confirm = vi.spyOn(window, 'confirm')
    expect(confirmLeaveIfBlocked()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('allows navigation when the blocker reports clean', () => {
    setNavBlocker(() => false)
    const confirm = vi.spyOn(window, 'confirm')
    expect(confirmLeaveIfBlocked()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('prompts and returns the confirm result when the blocker reports dirty', () => {
    setNavBlocker(() => true)
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    expect(confirmLeaveIfBlocked()).toBe(false)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    expect(confirmLeaveIfBlocked()).toBe(true)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/navGuard.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement navGuard**

Create `event-organizer-frontend/src/modules/shared/navGuard.ts`:

```ts
// A single active navigation blocker, registered by whichever screen has
// unsaved changes. navigateTo (and the logout handler) consult it before
// leaving so the user can confirm losing edits.
let blocker: (() => boolean) | null = null

export function setNavBlocker(fn: (() => boolean) | null): void {
  blocker = fn
}

// true = navigation may proceed (nothing to lose, or the user confirmed).
export function confirmLeaveIfBlocked(): boolean {
  if (blocker && blocker()) {
    return window.confirm('Есть несохранённые изменения. Уйти без сохранения?')
  }
  return true
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/navGuard.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing routing test**

Create `event-organizer-frontend/src/modules/shared/routing.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { navigateTo } from './routing.ts'
import { setNavBlocker } from './navGuard.ts'

beforeEach(() => {
  window.history.replaceState(null, '', '/schedule')
})
afterEach(() => {
  setNavBlocker(null)
  vi.restoreAllMocks()
})

describe('navigateTo guard', () => {
  it('does not navigate when a dirty blocker is declined', () => {
    setNavBlocker(() => true)
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/schedule')
  })

  it('navigates when a dirty blocker is confirmed', () => {
    setNavBlocker(() => true)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/bookings')
  })

  it('skips the guard for same-path navigation', () => {
    setNavBlocker(() => true)
    const confirm = vi.spyOn(window, 'confirm')
    navigateTo('/schedule')
    expect(confirm).not.toHaveBeenCalled()
  })

  it('skips the guard when skipGuard is set', () => {
    setNavBlocker(() => true)
    const confirm = vi.spyOn(window, 'confirm')
    navigateTo('/login', { replace: true, skipGuard: true })
    expect(confirm).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe('/login')
  })
})
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/routing.test.ts`
Expected: FAIL — `navigateTo` ignores the guard / rejects `skipGuard`.

- [ ] **Step 7: Add the guard to `navigateTo`**

Replace `routing.ts`'s `navigateTo` with:

```ts
import { confirmLeaveIfBlocked } from './navGuard.ts'

export function navigateTo(path: string, options?: { replace?: boolean; skipGuard?: boolean }): void {
  const leavingCurrent = path !== window.location.pathname
  if (leavingCurrent && !options?.skipGuard && !confirmLeaveIfBlocked()) return
  const method = options?.replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', path)
  window.dispatchEvent(new Event('app:navigate'))
}
```

(Keep the `AppRoute`/`parseRoute` exports unchanged; add the `import` at the top.)

- [ ] **Step 8: Run to verify it passes**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/routing.test.ts src/modules/shared/navGuard.test.ts`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add event-organizer-frontend/src/modules/shared/navGuard.ts event-organizer-frontend/src/modules/shared/navGuard.test.ts event-organizer-frontend/src/modules/shared/routing.ts event-organizer-frontend/src/modules/shared/routing.test.ts
git commit -m "feat(organizer-fe): navigation guard for unsaved changes"
```

---

### Task 3: Wire SchedulePage — single save, dirty markers, guards

**Files:**
- Modify: `event-organizer-frontend/src/modules/schedule/SchedulePage.tsx`
- Modify: `event-organizer-frontend/src/index.css`
- Test: `event-organizer-frontend/src/modules/schedule/SchedulePage.test.tsx`

**Interfaces:**
- Consumes: `computeDirty` (Task 1), `setNavBlocker` (Task 2), `putSchedule`/`putTravel`/`buildUpsert`/`buildTravel`/`validate` (existing).

- [ ] **Step 1: Read the current test file to preserve existing cases**

Read `event-organizer-frontend/src/modules/schedule/SchedulePage.test.tsx` so the new assertions extend (not replace) the current mount helper and mocks.

- [ ] **Step 2: Rewrite SchedulePage.tsx**

Replace the file with:

```tsx
import { useEffect, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { setNavBlocker } from '../shared/navGuard.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { WeeklyHours } from './WeeklyHours.tsx'
import { DateOverrides } from './DateOverrides.tsx'
import { Travel } from './Travel.tsx'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'
import { bundleToState, buildTravel, buildUpsert, computeDirty, emptyDays, validate, type EditorState } from './schedule.ts'

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function upstreamMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 502) return 'Сервис временно недоступен. Попробуйте ещё раз.'
  if (err instanceof ApiError) return err.message
  return 'Не удалось сохранить. Попробуйте ещё раз.'
}

function DirtyBadge({ show }: { show: boolean }) {
  if (!show) return null
  return <span className="dirty-badge">не сохранено</span>
}

export function SchedulePage() {
  const [state, setState] = useState<EditorState | null>(null)
  const [saved, setSaved] = useState<EditorState | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [savedOk, setSavedOk] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    const defaultTz = browserTz()
    getSchedule()
      .then((bundle) => {
        if (cancelled) return
        const next = bundleToState(bundle, defaultTz)
        setState(next)
        setSaved(next)
      })
      .catch(() => {
        if (cancelled) return
        const fallback = { name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] }
        setState(fallback)
        setSaved(fallback)
        setErrors(['Не удалось загрузить расписание'])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const dirty = useMemo(() => (state && saved ? computeDirty(state, saved) : null), [state, saved])

  useEffect(() => {
    setNavBlocker(() => Boolean(dirty?.any))
    return () => setNavBlocker(null)
  }, [dirty?.any])

  useEffect(() => {
    if (!dirty?.any) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty?.any])

  if (loading || !state || !saved || !dirty) {
    return <div className="card">Загрузка…</div>
  }

  function edit(next: EditorState) {
    setSavedOk(false)
    setState(next)
  }

  async function handleSave() {
    if (!state || !dirty) return
    setSavedOk(false)
    const validationErrors = validate(state)
    if (validationErrors.length > 0) {
      setErrors(validationErrors)
      return
    }
    setErrors([])
    setSaving(true)
    try {
      if (dirty.schedule) await putSchedule(buildUpsert(state))
      if (dirty.travel) await putTravel(buildTravel(state))
      setSaved(state)
      setSavedOk(true)
    } catch (err) {
      setErrors([upstreamMessage(err)])
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Расписание</h1>
        <button type="button" onClick={handleSave} disabled={saving || !dirty.any}>
          Сохранить
        </button>
      </div>

      {errors.length > 0 && (
        <div className="section">
          {errors.map((e) => (
            <p className="error-text" key={e}>
              {e}
            </p>
          ))}
        </div>
      )}
      {savedOk && !dirty.any && <p className="ok-text">Сохранено</p>}

      <div className={`section${dirty.tz ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Часовой пояс</h2>
          <DirtyBadge show={dirty.tz} />
        </div>
        <TimeZoneField value={state.timeZone} onChange={(tz) => edit({ ...state, timeZone: tz })} />
      </div>

      <div className={`section${dirty.weekly ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Часы по неделям</h2>
          <DirtyBadge show={dirty.weekly} />
        </div>
        <WeeklyHours days={state.days} onChange={(days) => edit({ ...state, days })} />
      </div>

      <div className={`section${dirty.overrides ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Исключения по датам</h2>
          <DirtyBadge show={dirty.overrides} />
        </div>
        <DateOverrides overrides={state.overrides} onChange={(overrides) => edit({ ...state, overrides })} />
      </div>

      <div className={`section${dirty.travel ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Поездки (временный часовой пояс)</h2>
          <DirtyBadge show={dirty.travel} />
        </div>
        <Travel travels={state.travels} onChange={(travels) => edit({ ...state, travels })} />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Add the section-head + dirty CSS**

In `index.css`, add:

```css
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.section.is-dirty {
  box-shadow: inset 3px 0 0 var(--primary);
}
.dirty-badge {
  font-size: 12px;
  font-weight: 500;
  color: var(--primary);
  background: var(--primary-pale, rgba(79, 110, 242, 0.1));
  border-radius: 999px;
  padding: 2px 10px;
}
```

(The section headers were bare `<h2>`; they are now wrapped in `.section-head` so the badge sits to the right.)

- [ ] **Step 4: Update SchedulePage tests**

Adjust `SchedulePage.test.tsx`. Keep the existing mount/mocks; change/add:

```tsx
it('shows a single save button labelled Сохранить', async () => {
  await mountPage(/* existing bundle */)
  const buttons = [...container.querySelectorAll('button')].map((b) => b.textContent)
  expect(buttons.filter((t) => t?.includes('Сохранить'))).toEqual(['Сохранить'])
  expect(buttons).not.toContain('Сохранить поездки')
})

it('marks the time-zone section dirty after an edit and enables save', async () => {
  await mountPage(/* existing bundle */)
  const saveBtn = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить') as HTMLButtonElement
  expect(saveBtn.disabled).toBe(true)
  // edit the tz via the tz combobox input
  const tzInput = container.querySelector('.tz-picker-input') as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    tzInput.focus()
    setter.call(tzInput, 'Берл')
    tzInput.dispatchEvent(new Event('input', { bubbles: true }))
  })
  // pick the first option
  const opt = document.querySelector('.tz-option') as HTMLElement
  await act(async () => opt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })))
  expect(container.querySelector('.section.is-dirty')).not.toBeNull()
  expect(saveBtn.disabled).toBe(false)
})

it('saves only the travel endpoint when only travel changed', async () => {
  // bundle with a travel row already; mount, edit a travel field, click save
  // assert putTravel called, putSchedule NOT called (spy on the api module fns)
})
```

Use the file's existing pattern for mocking `scheduleApi` (`getSchedule`/`putSchedule`/`putTravel`) — mock all three; spy to assert which was called. If the current test mocks via `vi.mock('./scheduleApi.ts', …)`, extend that mock to expose `putSchedule`/`putTravel` spies. Match the tz-combobox interaction already used elsewhere if present; otherwise the `.tz-picker-input` + `.tz-option` mousedown flow above works (portaled option is in `document`, not `container`).

- [ ] **Step 5: Run the schedule suite + full suite + build + lint**

Run: `cd event-organizer-frontend && npx vitest run && npm run build && npm run lint`
Expected: all PASS, tsc clean, eslint clean.

- [ ] **Step 6: Commit**

```bash
git add event-organizer-frontend/src/modules/schedule/SchedulePage.tsx event-organizer-frontend/src/index.css event-organizer-frontend/src/modules/schedule/SchedulePage.test.tsx
git commit -m "feat(organizer-fe): single save + per-section unsaved markers + leave guard"
```

---

### Task 4: Route logout through the guard; auth redirects bypass it

**Files:**
- Modify: `event-organizer-frontend/src/modules/app/OrganizerLayout.tsx`
- Modify: `event-organizer-frontend/src/App.tsx`
- Test: `event-organizer-frontend/src/modules/app/OrganizerLayout.test.tsx` (create if absent; else extend)

**Interfaces:**
- Consumes: `confirmLeaveIfBlocked` (Task 2), `navigateTo(..., { skipGuard })` (Task 2).

- [ ] **Step 1: Guard logout in OrganizerLayout**

In `OrganizerLayout.tsx`, import `confirmLeaveIfBlocked` from `../shared/navGuard.ts` and change `handleLogout`:

```tsx
  function handleLogout() {
    if (!confirmLeaveIfBlocked()) return
    logout()
    navigateTo('/login', { replace: true, skipGuard: true })
  }
```

(Menu buttons keep the default guarded `navigateTo(item.path)`.)

- [ ] **Step 2: Bypass the guard on App's auth redirects**

In `App.tsx`, the two auth-redirect `navigateTo` calls become:

```tsx
    if (!isAuthenticated && route.name !== 'login') {
      navigateTo('/login', { replace: true, skipGuard: true })
      return
    }
    if (isAuthenticated && route.name === 'login') {
      navigateTo('/', { replace: true, skipGuard: true })
    }
```

(The not-found "Вернуться к расписанию" button stays a plain `navigateTo('/', { replace: true })` — the schedule page is unmounted there, so the blocker is null anyway.)

- [ ] **Step 3: Write the OrganizerLayout logout-guard test**

Create/extend `event-organizer-frontend/src/modules/app/OrganizerLayout.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { OrganizerLayout } from './OrganizerLayout.tsx'
import { setNavBlocker } from '../shared/navGuard.ts'

// Minimal auth provider stub if OrganizerLayout needs one — mirror the pattern
// used by other component tests (mock '../auth/useAuth.ts' so logout is a spy).
const logout = vi.fn()
vi.mock('../auth/useAuth.ts', () => ({ useAuth: () => ({ logout, jwtToken: null }) }))

let container: HTMLDivElement
let root: Root
async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<OrganizerLayout pathname="/schedule">x</OrganizerLayout>))
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  setNavBlocker(null)
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe('OrganizerLayout logout guard', () => {
  it('does not log out when a dirty blocker is declined', async () => {
    setNavBlocker(() => true)
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await mount()
    const btn = container.querySelector('button[aria-label="Выйти"]') as HTMLButtonElement
    await act(async () => btn.click())
    expect(logout).not.toHaveBeenCalled()
  })

  it('logs out when a dirty blocker is confirmed', async () => {
    setNavBlocker(() => true)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await mount()
    const btn = container.querySelector('button[aria-label="Выйти"]') as HTMLButtonElement
    await act(async () => btn.click())
    expect(logout).toHaveBeenCalledTimes(1)
  })
})
```

Verify the mock path/shape matches the repo's existing component-test convention (check another `*.test.tsx` that mocks `useAuth`); adjust the mock to whatever `OrganizerLayout` actually consumes (it uses `useAuth().logout` and `.jwtToken`).

- [ ] **Step 4: Run the suite + build + lint**

Run: `cd event-organizer-frontend && npx vitest run && npm run build && npm run lint`
Expected: all PASS, tsc clean, eslint clean.

- [ ] **Step 5: Commit**

```bash
git add event-organizer-frontend/src/modules/app/OrganizerLayout.tsx event-organizer-frontend/src/App.tsx event-organizer-frontend/src/modules/app/OrganizerLayout.test.tsx
git commit -m "feat(organizer-fe): logout respects the unsaved-changes guard; auth redirects bypass it"
```

---

### Task 5: Docs

**Files:**
- Modify: `event-organizer-frontend/CLAUDE.md`

- [ ] **Step 1: Document the save/guard model**

Under the `schedule/` screen description in `CLAUDE.md`, add: the page has a
single "Сохранить" that persists only the changed slice(s) (`putSchedule`
and/or `putTravel`); unsaved sections are marked (`.section.is-dirty` +
"не сохранено" badge); leaving with unsaved changes warns via `beforeunload`
(close/refresh) and `confirmLeaveIfBlocked` (menu/logout, through
`shared/navGuard.ts` + `navigateTo`'s `skipGuard` option).

- [ ] **Step 2: Commit**

```bash
git add event-organizer-frontend/CLAUDE.md
git commit -m "docs(organizer-fe): schedule single-save + unsaved-changes guard"
```

---

## Notes for the executor

- `event-organizer-frontend` is tracked by the **root** `events` repo. Commit from the repo root; never `git add -A` (it sweeps in embedded-repo gitlinks). Stage the exact files per task.
- `npm run build` runs `tsc -b`, which **does** typecheck test files — keep test types clean (definite-assignment `!` for module-level `let container`/`root` if a non-mounting test exists).
- The portaled tz dropdown renders into `document.body`, not the test `container` — query `.tz-option` on `document`.
- Re-verify the exact existing `SchedulePage.test.tsx` mock shape before editing; extend it rather than rewriting.
