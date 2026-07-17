# Roadmap & Status — Frontend design system, Booker UX, Configurable booking fields

**Last updated:** 2026-07-17. This document records the recent development arc (what shipped,
what's merged, what's next) so work can be resumed cleanly. It complements the per-feature
specs in `docs/superpowers/specs/` and plans in `docs/superpowers/plans/`.

## TL;DR

Five feature lines are **done and reviewed** (four merged to `main`; the fifth — Phase 3 — built
on branches, pending merge):

1. **`events-design-system`** — a shared, self-contained light design package (CSS tokens +
   stylesheet + generic React components), published as a git-tag dependency `v0.1.0`.
2. **Compact calendar** in the public Booker (react-day-picker month grid + slots column).
3. **Configurable booking fields — Phase 1** (event-scheduling: model + validation + management API
   + answers on bookings).
4. **Configurable booking fields — Phase 2** (public rendering: size hardening + event-booker BFF
   surfaces fields/forwards answers + dynamic guest form).
5. **Configurable booking fields — Phase 3** (admin editor: event-admin `/api/scheduling/*` proxy +
   admin-frontend "Поля записи" editor). **The feature is now end-to-end** — admins configure fields
   in a UI instead of via the API. Built on `feat/booking-fields-phase3` branches (root events,
   event-admin, event-admin-frontend); reviewed clean per task; **not yet merged**.

**Next:** merge the Phase-3 branches, then the post-Phase-3 follow-up: **viewing** the collected
answers (admin booking detail / organizer ЛК / notification emails) — the answers are already
stored + on the `booking.lifecycle` payload; only the display UIs are missing.

---

## What shipped (merged)

### 1. `events-design-system` (shared design package)
- **Repo:** `github.com/Lelikov/events-design-system`, tagged **`v0.1.0`**. Also a directory
  `events-design-system/` in this monorepo (its own git repo, like `event-schemas`).
- **Ships:** `styles.css` (full light stylesheet — tokens + reset + components, extracted verbatim
  from the admin frontend's design), `tokens.css` (CSS variables + Plus Jakarta Sans font), and five
  generic React components — `Icon`, `Switch`, `ErrorBoundary` (Sentry-decoupled via an `onError`
  prop), `Badge`, `UserInfoView` — built with `tsup` to `dist/`.
- **Distribution:** git-tag npm dependency (`"events-design-system": "github:Lelikov/events-design-system#v0.1.0"`);
  `file:../events-design-system` for local dev (run `npm install` inside the package first — see its README).
- **Consumers (all migrated onto it, one unified light theme):**
  - `event-admin-frontend` — full `styles.css`; rendered identically by construction.
  - `event-booker-frontend` — full `styles.css`.
  - `jitsi-chat` — **dropped its dark theme**; uses `tokens.css` + stream-chat light theme (the full
    sheet's global `button{}` would collide with embedded stream-chat).
- **Spec:** `docs/superpowers/specs/2026-07-16-events-design-system-design.md` ·
  **Plan:** `docs/superpowers/plans/2026-07-16-events-design-system.md`.

### 2. Booker compact calendar
- `event-booker-frontend`'s slot step was a tall vertical list of every day's slots; it is now a
  compact cal.com-style **month calendar** (`react-day-picker`) where only available days are
  selectable, plus a slots column for the picked day. Auto-selects the first available day; fetches
  availability per displayed month; step-scoped wide shell so only the slot step is wide.
- **Spec:** `.../specs/2026-07-16-booker-compact-calendar-design.md` ·
  **Plan:** `.../plans/2026-07-16-booker-compact-calendar.md`.
- **Deferred follow-up:** thread the selected timezone through day-identity (`dateKey`/`today`/
  `formatDayLabel` via `Intl`) — a narrow midnight/cross-tz edge; no wrong booking results.

### 3. Configurable booking fields — Phase 1 (event-scheduling core)
Per-event-type custom "booking questions", configurable via API:
- **Migration `0006`:** `booking_field` table (per event type; 6 field types — `text`, `textarea`,
  `select`, `radio`, `checkbox`, `boolean`; `options` JSONB; `position`) + `booking.field_answers`
  JSONB snapshot.
- **`booking_fields/` module:** frozen DTOs; pure validation/slug/snapshot logic (`slugify_key`
  incl. Cyrillic→latin, collision-safe key dedupe, `validate_field_items`, `validate_and_snapshot`);
  DB adapter (list / atomic replace-all) + controller.
- **Management API:** `GET`/`PUT /api/v1/event-types/{id}/booking-fields` (ordered replace-all;
  `422` invalid / `404` unknown); `booking_fields` exposed on the event-type read.
- **Booking-create:** validates answers authoritatively (fail-fast → `422`), stores an immutable
  snapshot `[{key,label,type,value}]` on `booking.field_answers`, echoes it on the `booking.created`
  event payload (additive; safe for event-saver).
- **Spec:** `.../specs/2026-07-17-configurable-booking-fields-design.md` (covers all 3 phases) ·
  **Plan:** `.../plans/2026-07-17-booking-fields-phase1-scheduling.md`.

### 4. Configurable booking fields — Phase 2 (public rendering)
- **Hardening (event-scheduling):** size caps in the authoritative validators — ≤50 fields, ≤100
  options, label ≤200, placeholder ≤500, option ≤200, text answer ≤10 000 (the pre-public-exposure
  requirement).
- **BFF (event-booker):** the single event-type read carries `booking_fields`; booking-create
  forwards `answers` verbatim; an upstream validation `422` surfaces to the guest as a `422`
  (not an opaque `502`). event-scheduling stays the sole validator — the BFF does not re-validate.
- **Frontend (event-booker-frontend):** `GuestForm` renders a control per field type
  (text/textarea/select/radio/multi-checkbox/boolean), client-validates (mirroring the server),
  and submits `answers`; `BookingFlowPage` threads fields + answers; text inputs cap at the server
  limit. Name + email remain built-in.
- **Plan:** `.../plans/2026-07-17-booking-fields-phase2-public.md`.

### 5. Configurable booking fields — Phase 3 (admin editor)
- **event-admin (new upstream):** first `event-scheduling` client in this service — an
  `ISchedulingClient`/`SchedulingClient` httpx proxy (`EVENT_SCHEDULING_URL` + `SCHEDULING_API_KEY`,
  `Authorization: Bearer`), DI-provided like the notifier client. Three admin-JWT routes under
  `/api/scheduling/*` (`GET event-types`, `GET`/`PUT event-types/{id}/booking-fields`) that
  **forward verbatim** and map upstream errors to `scheduling_service_error` **preserving the
  status code** — event-scheduling stays the sole validator (`404`/`422` reach the frontend).
- **event-admin-frontend:** a "Поля записи" screen (sidebar → `/booking-fields`) — pick an event
  type, then an editor to add/remove/reorder fields and set `field_type`/`label`/`placeholder`/
  `required`/`options` (6 types; option types get an inline options sub-editor). Client validation
  mirrors the server (non-empty label, ≥1 distinct option); Save = `PUT` the ordered list; an
  upstream `422` surfaces inline and keeps the form filled. Built on the design system (`Icon`/
  `Switch`, `.card`/`.field`/`.error-text`).
- **Plan:** `.../plans/2026-07-17-booking-fields-phase3-admin-editor.md`. Reviewed clean per task
  (proxy 11/11 tests; frontend module 18/18, full suite 90/90). **On branches, pending merge.**

### (Earlier in the same arc) `event-organizer` BFF — slice 6.1
- Organizer cabinet BFF (password + JWT auth, own DB, port 8006): `/api/me/*` proxies event-scheduling
  (schedule, bookings) + event-users (profile) with the user id always from the JWT session
  ("ownership by construction"). Merged. Its frontend SPA (slice 6.2) is not built.

---

## Repo / merge state (as of this doc)

Everything above is merged and pushed:

| Repo | Branch merged to | Notes |
|---|---|---|
| `events` (root; event-scheduling, event-booker, event-booker-frontend, event-organizer, events-design-system dir, docs) | `main` | one linear merge of the whole stack |
| `events-design-system` | `main` + tag `v0.1.0` | published for git-tag consumption |
| `event-admin-frontend` (nested) | `feature/ui-redesign` | its active line |
| `jitsi-chat` (nested) | `main` | dark theme dropped |

Local feature branches were deleted after merge. `event-booking` and `event-users` are their own
nested repos, untouched by this arc.

---

## Next: merge Phase 3, then surface the collected answers

**Merge the Phase-3 branches** (`feat/booking-fields-phase3` in root events, event-admin, and
event-admin-frontend). Root events carries the plan + the `docker-compose.services.yml` wiring
(event-admin gets `EVENT_SCHEDULING_URL`/`SCHEDULING_API_KEY` + a `depends_on`); event-admin carries
the proxy; event-admin-frontend carries the editor (branch off its active `feature/ui-redesign`).
Manual UI smoke (needs an admin login, so run it yourself): admin UI → **Поля записи** → pick a
type → add a required `textarea` → Save → open that type in the Booker and confirm it renders +
submits (closes the loop Phases 1–2 already built).

**Then — viewing answers (the natural follow-up):** answers are already stored on
`booking.field_answers` and echoed on the `booking.created` payload, but no UI surfaces them. Add a
read view in the admin booking detail (and/or organizer ЛК / notification emails). This consumes the
stored snapshot / event payload — no re-collection needed.

---

## Deferred / future work (tracked, not lost)

**Configurable booking fields — post-Phase-3 follow-ups:**
- **Viewing answers** — the answers are stored + on the `booking.created` payload, but no UI yet
  surfaces them (admin booking detail, organizer ЛК, or notification emails). A follow-up slice.
- Anti-abuse on the public `POST /bookings` (rate-limit/CAPTCHA; answer count/body-size caps beyond
  the per-value length cap) — the BFF documents this deferral.
- Reschedule/cancel CloudEvent bodies don't re-echo answers (only `booking.created` does).
- Optional fields left empty (incl. boolean `false`) are omitted, not stored as an explicit "no"
  (intentional; product confirmation if a recorded "no" is ever needed).

**Booker / frontend:**
- Compact-calendar timezone day-identity edge (see §2 above).
- Booker 4b post-merge minors (nav-during-submit, dead step value) from the earlier Booker slice.

**cal.com replacement (broader program — see the project memory for the full slice history):**
- **Slice 5 SSRF hardening** on `event-scheduling/calendar/ical_client.py::fetch` — REQUIRED
  pre-production blocker (resolve host & reject private/loopback/metadata IPs; allowlist; re-validate
  redirect hops). Own mini-slice or fold into OAuth calendar sync (slice 5.2).
- **Slice 6.2** — `event-organizer-frontend` SPA (the organizer cabinet UI over the 6.1 BFF).
- Calendar OAuth providers (Google/Office), export (booking→calendar), CalDAV.

**events-design-system follow-ups (from its final review):**
- Give the `user` icon a distinct glyph (currently identical to `users`).
- Export `SwitchProps`/`ErrorBoundaryProps`/`UserInfoViewProps` for consumer wrappers.
- Self-host the font (currently Google Fonts CDN) if a consumer needs strict CSP / offline.

---

## How to resume / run things

- **Specs/plans:** `docs/superpowers/specs/` and `docs/superpowers/plans/` (dated files).
- **Local demo:** `docker compose up -d --build` from the repo root brings up the stack. To preview
  the migrated frontends' new design, run their Vite dev servers (`npm run dev`) — their Docker image
  builds now need git auth for the private `events-design-system` dep, so dev servers are the easy
  path (admin/booker/jitsi). Admin login: seed `admin_users` (see root `CLAUDE.md`) →
  `admin@example.com` / `Admin123!` / TOTP secret `JBSWY3DPEHPK3PXP`.
- **event-scheduling tests:** need a Postgres; the suite uses `TEST_POSTGRES_DSN` (e.g. a throwaway
  `docker run --rm -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_scheduling -p 5602:5432
  postgres:16`, then `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5602/event_scheduling'`).
- **Booking fields end-to-end (manual):** `PUT /api/v1/event-types/{id}/booking-fields` on
  event-scheduling (Bearer `SCHEDULING_API_KEY`) to configure fields, then open the Booker's booking
  page for that event type — the fields render on the guest step and the answers submit + store.
