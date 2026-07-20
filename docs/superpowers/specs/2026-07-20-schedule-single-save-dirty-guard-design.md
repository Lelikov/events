# Schedule page: single save + unsaved-changes highlight + leave guard

**Date:** 2026-07-20
**Status:** design locked with the conservative options (user stepped away mid-brainstorm; see Decision Log)

## Problem

The organizer schedule page (`event-organizer-frontend`) has **two** save
buttons — one in the page header (schedule bundle) and one inside the "Поездки"
section (travel) — because they hit two endpoints (`PUT /api/me/schedule` and
`PUT /api/me/schedule/travel`). Two save buttons on one screen is confusing.
There is also no indication of unsaved changes and no protection against losing
edits by navigating away.

Three asks:

1. One save button instead of two.
2. Highlight which data is not yet saved after edits.
3. On leaving the page (close/refresh, in-app menu navigation, logout) with
   unsaved changes, show a warning.

## Scope

`event-organizer-frontend` only:
- `schedule/SchedulePage.tsx`, `schedule/schedule.ts` (dirty helper),
- `shared/routing.ts` + a new `shared/navGuard.ts` (in-app navigation guard),
- `app/OrganizerLayout.tsx` (logout goes through the guard), `App.tsx` (auth
  redirects bypass the guard), `index.css`.

Out of scope: the BFF/domain (endpoints unchanged), other pages, a custom modal
(native `confirm` is used — see Decision Log).

## Design

### 1. One save button that persists only the dirty parts

Remove the "Сохранить поездки" button. Keep the single header "Сохранить".
`handleSave` validates the schedule part (`validate(state)`), then:

- if the **schedule part** is dirty (time zone / weekly hours / date overrides)
  → `await putSchedule(buildUpsert(state))`,
- if the **travel part** is dirty → `await putTravel(buildTravel(state))`,
- on success, set the saved baseline to the current state (clearing dirty) and
  show "Сохранено".

Saving only the dirty part avoids a spurious `schedule_change_log` snapshot on
every save when only travel changed (the domain appends one on every
`PUT /schedule`). The button is disabled when nothing is dirty or while saving.
Errors from either call are shown in the single top error block (the separate
`travelError` state is removed).

### 2. Dirty tracking + per-section highlight

Keep a `saved: EditorState` baseline (set on load and after each successful
save). A pure helper in `schedule.ts`:

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

Comparing the full slices (including client `uid`s) is correct: the baseline is
a snapshot of the same loaded state, so unchanged rows keep identical uids;
editing a value, adding, removing, or reordering a row all diverge from the
baseline. `name` is never edited in the UI, so it is not tracked.

Each of the four `.section`s maps to one flag (`tz`→Часовой пояс,
`weekly`→Часы по неделям, `overrides`→Исключения по датам, `travel`→Поездки).
A dirty section gets `className="section is-dirty"` and a header badge
`<span className="dirty-badge">не сохранено</span>`. The Save button reflects
`dirty.any` (disabled when false).

### 3. Leave guard (two channels)

**Close / refresh / tab close** — a `beforeunload` listener registered while
`dirty.any`, calling `e.preventDefault(); e.returnValue = ''` so the browser
shows its native "Leave site?" prompt. (Browsers do not allow a custom UI here.)

**In-app navigation (menu, logout)** — a tiny module `shared/navGuard.ts`:

```ts
let blocker: (() => boolean) | null = null
export function setNavBlocker(fn: (() => boolean) | null): void { blocker = fn }
// true = navigation may proceed (nothing to lose, or the user confirmed leaving).
export function confirmLeaveIfBlocked(): boolean {
  if (blocker && blocker()) return window.confirm('Есть несохранённые изменения. Уйти без сохранения?')
  return true
}
```

`routing.ts` `navigateTo(path, options?)` gains a `skipGuard?: boolean` option
and consults the guard, skipping it for same-path navigations (clicking the
already-active menu item must not prompt):

```ts
export function navigateTo(path: string, options?: { replace?: boolean; skipGuard?: boolean }): void {
  const leavingCurrent = path !== window.location.pathname
  if (leavingCurrent && !options?.skipGuard && !confirmLeaveIfBlocked()) return
  const method = options?.replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', path)
  window.dispatchEvent(new Event('app:navigate'))
}
```

Wiring:
- `SchedulePage` registers the blocker in a `[dirty.any]` effect
  (`setNavBlocker(() => dirty.any)`; cleanup `setNavBlocker(null)`), and the
  `beforeunload` listener in the same/adjacent effect.
- `OrganizerLayout.handleLogout` runs the guard **before** clearing the session,
  then navigates with `skipGuard` (the session is already gone):
  `if (!confirmLeaveIfBlocked()) return; logout(); navigateTo('/login', { replace: true, skipGuard: true })`.
- `App.tsx` auth redirects (`unauth → /login`, `auth-on-login → /`) pass
  `skipGuard: true` — a session-driven redirect must not be trapped behind a
  confirm. The menu buttons (`OrganizerLayout` `NAV_ITEMS`) use the default
  guarded `navigateTo`.

## Data flow

Load: `getSchedule` → `bundleToState` → `state` **and** `saved` (same value).
Edit: `setState` mutates `state`; `dirty = computeDirty(state, saved)` recomputed
each render drives the badges, the Save button, the nav blocker, and
`beforeunload`. Save: PUT the dirty part(s) → on success `setSaved(state)` →
`dirty.any` becomes false → guards disarm, "Сохранено" shows.

## Error handling

- Validation (schedule part) blocks the save and lists messages in the top
  block, as today.
- A failed `putSchedule`/`putTravel` shows `upstreamMessage(err)` in the top
  block; the baseline is not advanced, so the section stays marked dirty.
- `confirmLeaveIfBlocked` only ever reads the blocker + shows a confirm; it
  never navigates itself.

## Testing

- **`schedule.ts` `computeDirty`**: identical state → all false; a tz change →
  `tz`+`schedule`+`any`; a weekly edit → `weekly`+`schedule`+`any`; an overrides
  edit → `overrides`; a travel edit → `travel`+`any` (schedule false).
- **`navGuard.ts`**: no blocker → `confirmLeaveIfBlocked()` true without calling
  confirm; blocker returning true → calls `window.confirm` (mocked) and returns
  its result; `setNavBlocker(null)` disarms.
- **`routing.ts`**: `navigateTo` to a different path with a dirty blocker and
  `confirm→false` does not pushState; `confirm→true` does; `skipGuard: true`
  bypasses; same-path navigation never prompts.
- **`SchedulePage.test`**: renders exactly one button labelled "Сохранить" (no
  "Сохранить поездки"); editing the time zone adds `is-dirty` to that section
  and enables Save; save with only travel dirty calls `putTravel` and not
  `putSchedule` (and vice-versa); after a successful save the section loses
  `is-dirty`.
- **`OrganizerLayout.test`**: with an armed blocker, logout does not proceed when
  `confirm→false`; proceeds (clears session, navigates) when `confirm→true`.

## Decision Log

- **Highlight granularity = per section** (not a whole-page banner, not
  per-row): directly answers "какие данные не сохранены" and is proportionate.
  Per-row diffing was rejected as over-built (YAGNI).
- **Warning UI = native `confirm` + native `beforeunload`** (not a custom DS
  modal): close/refresh are unavoidably native (browsers forbid custom UI), so a
  native confirm for menu/logout keeps a **consistent** warning across every
  leave channel instead of mixing a styled modal with a native prompt. It is
  also far simpler and more robust. A styled modal is a possible additive
  follow-up. Chosen while the user was away; low-regret.
- **Save only the dirty endpoint(s)** — avoids a spurious change-log snapshot on
  every save when only travel changed.
- **Guard skips same-path and `skipGuard` navigations** — clicking the active
  menu item, and session-driven auth redirects, must not prompt.
