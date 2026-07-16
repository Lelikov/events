# Booker Compact Calendar — Design Spec

**Date:** 2026-07-16
**Status:** Approved-in-brainstorm (pending spec review)
**Scope:** `event-booker-frontend` only (public Booker SPA). Frontend-only; no backend/API/contract changes.

## Goal

Replace the Booker's oversized slot picker — which renders **every day in a rolling 14-day window as a long vertical list of slot sections** — with a compact, cal.com-style month calendar: a month grid where only bookable days are selectable, and a slots column showing just the selected day's times. This shrinks the step from a full-page scroll to a single bordered card.

## Motivation

The current `SlotPicker` (`event-booker-frontend/src/modules/booking/SlotPicker.tsx`) stacks a `<h3>` + `.slot-grid` for **each** of ~14 days, producing a very tall page ("сейчас он очень большой получился"). The reference product (cal.com, `booking.zhivaya.org`) shows a compact three-region card — event info, a month calendar with only available days clickable, and a slots column for the picked day. This slice brings the Booker's slot step to that compact, scannable layout.

## Reference behaviour (cal.com, observed)

- Three regions in one card: `[event info | month calendar | time slots]`.
- Month grid; only days that have availability are clickable, others are dimmed/disabled; month nav arrows.
- Selecting a day populates a slots column (times, e.g. every 5 min) with a day header and a 12h/24h toggle.
- A timezone selector.
- Selecting a slot advances to the booking form.

This slice replicates the **layout and the available-day calendar behaviour**. The 12h/24h toggle and cal.com's heavier branding are out of scope (see Non-goals).

## Library choice

**`react-day-picker` v9** — headless, TypeScript-native, ~6M weekly downloads, the engine behind shadcn's calendar. It provides the month grid, month navigation, keyboard/ARIA accessibility, a `disabled` matcher, and a `modifiers`/`modifiersClassNames` system for marking available days — all themeable to our design tokens via its `--rdp-*` CSS variables and `classNames` prop. We build the surrounding layout and the slots column ourselves with the existing design-system CSS.

- New dependency in `event-booker-frontend`: `react-day-picker@^9`. If it requires a peer date lib (`date-fns`), add it per the library's install docs. No other new runtime deps.
- The month grid is the **only** thing the library owns. Layout, availability wiring, slots column, timezone, and data fetching remain our code.

## Architecture

Rewrite the slot step; everything else in the wizard is untouched.

- **`SlotPicker.tsx` → the compact calendar step.** Renders the three-region card:
  - **Event info panel** (left): event title, duration, and the timezone `<select>` (moved here from its current position). The event title/duration are passed in as props from `BookingFlowPage` (which already holds the chosen event type).
  - **Calendar** (middle): `<DayPicker mode="single" …>` — controlled `month`/`onMonthChange`, `selected`/`onSelect`, `disabled` matcher, `modifiers={{ available }}`, `startMonth`/`endMonth`.
  - **Slots column** (right): a day header + `.slot-grid` of `.slot-button`s for the selected day (reused verbatim from today).
- **Data flow:**
  - Fetch `getSlots(eventTypeId, monthStart, monthEnd, timeZone)` for the **currently displayed month** (clamped so `monthStart` is never before "now").
  - Derive `availableDays: Set<string>` = the `YYYY-MM-DD` keys present in the slots payload (the API already groups slots by local date). `available` matcher = `day => availableDays.has(key(day))`.
  - `disabled = day => isBeforeToday(day) || !availableDays.has(key(day))`.
  - On successful fetch, if no day is selected yet (or the selected day is no longer available), **auto-select the first available day** and show its slots.
  - `onMonthChange` updates the displayed month → triggers a new fetch for that month. Timezone change re-fetches the current month. A request-id guard (as in the current code) discards stale responses.
  - `onSelect(day)` sets the selected day → the slots column shows `slotsByDay[key(day)]`.
  - Clicking a slot calls the existing `onSelect(startTime)` prop (renamed to avoid clashing with the day-select handler — see Interfaces) to advance the wizard.
- **`BookingFlowPage.tsx`:** passes the event type's `title` and `duration_minutes` to `SlotPicker` for the info panel; otherwise unchanged (still owns the step machine SlotPicker → GuestForm → Confirmation).
- **`bookerApi.getSlots`:** unchanged signature; only the caller's window arguments change (per-month instead of rolling 14 days).

## Styling

- Import `react-day-picker/style.css` once (in the calendar component or `main.tsx`), then override its `--rdp-*` CSS variables and add `classNames` overrides in the Booker's local `App.css` to match the design system: selected day → `--primary`; today/available emphasis → `--primary-pale`; disabled → `--muted`; the grid text → `--text`. No CSS-in-JS.
- New local classes for the three-region card (e.g. `.cal-card`, `.cal-info`, `.cal-grid`, `.cal-slots`, `.cal-day-header`), built from design-system tokens and `.card`. The slots column reuses `.slot-grid`/`.slot-button` (already dark-text-correct after the design-system migration).
- Responsive: at ≤720px the three regions stack vertically (info → calendar → slots) via a media query; the calendar stays centered and legible.

## Interfaces

`SlotPicker` props change to carry event context and to disambiguate the two "select" concepts:

```ts
type Props = {
  eventTypeId: string
  eventTitle: string            // NEW — shown in the info panel
  durationMinutes: number       // NEW — shown in the info panel
  timeZone: string
  onTimeZoneChange: (tz: string) => void
  onSelectSlot: (startTime: string) => void   // RENAMED from onSelect (slot chosen → advance wizard)
}
```

Internal state: `displayMonth: Date`, `selectedDay: Date | null`, and the fetch result (`slotsByDay`, `availableDays`, loading/error) keyed by a request id.

## Error / edge handling

- **Loading:** the slots column shows a "Загрузка…" state while a month's slots are in flight; the calendar remains interactive.
- **Fetch error:** the existing `.banner-error` ("Не удалось загрузить слоты…") is shown in place of the slots column; the month can be re-navigated to retry.
- **Empty month:** every day disabled; slots column shows "Нет свободных слотов" and the user can page to the next month.
- **Month with no availability + no selection:** no auto-select; the slots column prompts to pick another day/month.
- **Timezone change:** re-fetch the current month; re-derive available days; keep the selected day if it is still available, else re-auto-select.
- **Race conditions:** stale responses (older request id) are discarded, mirroring the current implementation.
- **Booking window:** `startMonth = current month`; `endMonth` bounded to a sensible horizon (e.g. the current month + the number of months the `GET /slots` window cap allows) so users can't page into months the API won't serve.

## Testing

vitest + happy-dom (react-day-picker renders in happy-dom):

- `availableDays` derivation from a `getSlots` payload (days with ≥1 slot present, others absent).
- Past days and days with no slots are `disabled` (not clickable).
- Auto-select: on load, the first available day is selected and its slots render.
- Selecting a different available day swaps the slots column to that day's times.
- `onMonthChange` (next/prev) triggers a new `getSlots` call for the new month's range.
- Clicking a slot calls `onSelectSlot` with the slot's ISO start time.
- Timezone change re-fetches and re-derives availability.

Mock `getSlots` (as existing tests do); assert on rendered day/slot elements and the mock's call arguments.

## Non-goals (deferred)

- **Configurable booking fields** — the separate next slice (booking questions per event type, configured in event-admin, rendered in `GuestForm`). Not in this slice.
- **12h/24h toggle** — Russian users use 24h; keep 24h only for now.
- **"Add to calendar" / overlay-calendar / busy overlay**, and cal.com's heavier left-panel branding.
- **No changes** to `GuestForm`, `Confirmation`, routing, the BFF, or any API/contract.

## Rollout

Single frontend slice on a `event-booker-frontend` (root-tracked) branch: add the dependency, rewrite `SlotPicker`, adjust `BookingFlowPage` props, add the calendar CSS, add tests, keep the existing Booker suite green. It composes on top of the just-shipped `events-design-system` (booker already consumes `styles.css`).
