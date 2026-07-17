# Roadmap & Status — Frontend design system, Booker UX, Configurable booking fields

**Last updated:** 2026-07-17. This document records the recent development arc (what shipped,
what's merged, what's next) so work can be resumed cleanly. It complements the per-feature
specs in `docs/superpowers/specs/` and plans in `docs/superpowers/plans/`.

## TL;DR

Four feature lines are **done, reviewed, and merged to `main`** (plus the two nested-repo main lines):

1. **`events-design-system`** — a shared, self-contained light design package (CSS tokens +
   stylesheet + generic React components), published as a git-tag dependency `v0.1.0`.
2. **Compact calendar** in the public Booker (react-day-picker month grid + slots column).
3. **Configurable booking fields — Phase 1** (event-scheduling: model + validation + management API
   + answers on bookings).
4. **Configurable booking fields — Phase 2** (public rendering: size hardening + event-booker BFF
   surfaces fields/forwards answers + dynamic guest form).

**Next:** Configurable booking fields **Phase 3** — the event-admin editor UI. After that, the
feature is end-to-end (admin configures fields in a UI instead of via the API).

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

## Next: Configurable booking fields — Phase 3 (admin editor) — NOT STARTED

The final phase makes fields configurable in the **admin UI** instead of via the API. Design is
already captured in the Phase-spec (`.../specs/2026-07-17-configurable-booking-fields-design.md`,
§ "Admin config (Phase 3)"). Scope:

- **`event-admin` (new upstream — it does not talk to event-scheduling today):** add config
  (`EVENT_SCHEDULING_URL` + `SCHEDULING_API_KEY`, dev defaults matching the compose values used by
  event-booker/event-organizer), an `adapters/scheduling_client.py` (list event types, GET/PUT a
  type's booking-fields), and routes under the existing admin JWT auth
  (`GET /api/scheduling/event-types`, `GET`/`PUT /api/scheduling/event-types/{id}/booking-fields`).
- **`event-admin-frontend`:** a "Поля записи" screen — list event types → pick one → editor to
  add/remove/reorder fields and set `field_type`/`label`/`placeholder`/`required`/`options`; Save =
  `PUT` the ordered list. Uses the design-system components.
- **To resume:** brainstorm not needed (design is in the spec); go straight to writing the Phase-3
  plan, then subagent-driven execution. Branch off `main`.

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
