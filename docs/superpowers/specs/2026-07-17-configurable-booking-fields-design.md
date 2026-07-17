# Configurable Booking Fields — Design Spec

**Date:** 2026-07-17
**Status:** Approved-in-brainstorm (pending spec review)
**Scope:** Cross-service. `event-scheduling` (data + validation), `event-booker` BFF + `event-booker-frontend` (public rendering), `event-admin` + `event-admin-frontend` (admin config). Built in three self-standing phases.

## Goal

Let an admin configure, **per event type**, a set of extra "booking questions" (custom fields) that the public Booker then renders on its guest form; the guest's answers are validated and stored on the booking. Modelled on cal.com's booking questions (observed on `booking.zhivaya.org`: a required "Почему мне нужная помощь психолога" textarea). Name + email remain fixed built-ins; custom fields are added on top.

## Motivation

Different meeting types need different intake information (a psychology consult needs "describe your situation"; an intro call may need none). Today the Booker's guest form is hardcoded to name + email. This slice makes the extra fields data-driven per event type, configured in the admin panel — the mechanism the user asked for.

## Phasing

The plan is phased; each phase leaves the system working and testable:

1. **Phase 1 — `event-scheduling` core.** `booking_field` per event type; `field_answers` on `booking`; management API to read/replace a type's fields; public event-type read exposes fields; booking-create validates + stores answers. Configurable via API immediately (before any UI).
2. **Phase 2 — public rendering.** `event-booker` BFF surfaces the field defs on the event-type read and forwards `answers` on booking-create; `event-booker-frontend` renders the fields dynamically on the guest form with client-side validation.
3. **Phase 3 — admin config UI.** `event-admin` gains a proxy to `event-scheduling` (it has none today) — list event types, GET/PUT a type's booking fields; `event-admin-frontend` gets a booking-fields editor.

Phases 1+2 already deliver working configurable fields (configured via API/DB). Phase 3 adds the admin editor.

## Field types (MVP)

Six types. `select`/`radio`/`checkbox` carry `options` (`{value, label}[]`); the others do not.

| type | UI | value shape | options |
|---|---|---|---|
| `text` | single-line input | string | — |
| `textarea` | multi-line input | string | — |
| `select` | dropdown, choose one | string (a value) | yes |
| `radio` | radio group, choose one | string (a value) | yes |
| `checkbox` | checkbox group, choose any | string[] (subset of values) | yes |
| `boolean` | single checkbox (consent) | boolean | — |

Deferred field types (future, easy to add): `phone`, `number`, `date`, file upload.

## Data model (`event-scheduling`, migration `0006`)

**New table `booking_field`:**
- `id` UUID PK (`gen_random_uuid()`)
- `event_type_id` UUID FK → `event_type.id` `ON DELETE CASCADE`
- `field_key` TEXT — stable machine key, slug of the label; `UNIQUE (event_type_id, field_key)`
- `field_type` TEXT — `CHECK (field_type IN ('text','textarea','select','radio','checkbox','boolean'))`
- `label` TEXT
- `placeholder` TEXT NULL
- `required` BOOLEAN NOT NULL DEFAULT false
- `options` JSONB NULL — array of `{"value": str, "label": str}`; required for `select`/`radio`/`checkbox`, must be NULL/empty for others
- `position` INTEGER NOT NULL — display order within the event type
- `created_at` / `updated_at` timestamptz defaults
- `CHECK`: options present iff type is an option type (enforced in the service layer; the DB keeps the column nullable).

**New column on `booking`:** `field_answers` JSONB NOT NULL DEFAULT `'[]'` — a **snapshot** array captured at booking time:
```json
[{"key": "reason", "label": "Почему нужна помощь", "type": "textarea", "value": "…"},
 {"key": "topics", "label": "Темы", "type": "checkbox", "value": ["anxiety", "sleep"]}]
```
Snapshotting the label/type/value means later edits to `booking_field` never alter or corrupt historical bookings.

## API contracts (`event-scheduling`, all under `require_api_key`)

**Read a type's fields (management):** `GET /api/v1/event-types/{id}/booking-fields`
→ `{"items": [{field_key, field_type, label, placeholder, required, options, position}, …]}` ordered by `position`.

**Replace a type's fields (management):** `PUT /api/v1/event-types/{id}/booking-fields`
Body `{"items": [{field_type, label, placeholder?, required?, options?}, …]}` — an **ordered replace-all** (mirrors the schedule weekly-hours upsert style). The server:
- derives `field_key` = slug(label), de-duplicating within the type (`reason`, `reason-2`, …);
- assigns `position` from array order;
- validates each item (option types must have ≥1 option with non-empty distinct values; non-option types must omit options; label non-empty);
- replaces the type's existing rows transactionally.
→ returns the stored list (same shape as GET). `404` if the event type doesn't exist; `422` on invalid items.

**Public event-type read (existing endpoint, extended):** `GET /api/v1/event-types/{id}` response gains
`"booking_fields": [{field_key, field_type, label, placeholder, required, options, position}, …]`
(no internal `id`; `field_key` is the stable public identifier). This flows through the BFF to the Booker.

**Booking create (existing endpoint, extended):** the create-booking request gains
`"field_answers": [{"key": str, "value": str | str[] | bool}, …]` (optional; absent = `[]`).
`event-scheduling` is the **authoritative validator** — before inserting the booking it checks, against the type's current `booking_field` rows:
- every `required` field has a present, non-empty answer (non-empty string / non-empty list / boolean present);
- `select`/`radio` value ∈ the field's option values; `checkbox` values ⊆ option values (and unique); `boolean` is a real bool; `text`/`textarea` is a string;
- unknown answer keys (not a field of this type) are rejected;
On success it stores the **snapshot** (resolving each answer's `label`/`type` from the field def) into `booking.field_answers`. Validation failure → `422` with a field-keyed error. The stored snapshot is also included in the emitted `booking.lifecycle` event payload (cheap future-proofing for downstream display/notifications — no consumer changes required now).

## Public rendering (Phase 2)

**`event-booker` BFF:**
- The **single** event-type read (`GET /api/public/event-types/{id}`, used by the booking page) gains `booking_fields: list[BookingFieldModel]` (`field_key`, `field_type`, `label`, `placeholder`, `required`, `options`); `EventTypeDTO` carries them from `event-scheduling`. No internal ids leak. The event-type **list** (`GET /api/public/event-types`) stays lean — it does not need fields (use a detail model or an empty/omitted `booking_fields` on the list).
- `CreateBookingPublicRequest` gains `answers: list[AnswerModel]` (`{key, value}`); `GuestBookingService.book(...)` takes `answers` and passes them to `scheduling_client.create_booking(..., field_answers=…)`. The BFF does **not** re-validate (event-scheduling is authoritative); it forwards, and surfaces the `422` as the existing `{"detail": …}` error.

**`event-booker-frontend`:**
- `bookerApi` types: `EventType` gains `booking_fields`; `CreateBookingBody` gains `answers`.
- `BookingFlowPage` threads the loaded `eventType.booking_fields` into `GuestForm` and includes `answers` in `createBooking`.
- `GuestForm` renders name + email (built-ins) then a dynamic field per `booking_field`, by type (`text`/`textarea` → input/textarea; `select` → `<select>`; `radio` → radio group; `checkbox` → checkbox group; `boolean` → single checkbox). Client-side validation mirrors the server (required, option membership) for UX; the server remains authoritative. Styling reuses the `events-design-system` form classes (`.field`, `.field-error`, existing inputs).

## Admin config (Phase 3)

**`event-admin` (new upstream — it has no event-scheduling client today):**
- Config: `EVENT_SCHEDULING_URL` + `SCHEDULING_API_KEY` (dev defaults matching the compose values used by `event-booker`/`event-organizer`).
- `adapters/scheduling_client.py` (httpx, Bearer `SCHEDULING_API_KEY`): `list_event_types()`, `get_booking_fields(event_type_id)`, `put_booking_fields(event_type_id, items)`.
- Routes (behind the existing admin JWT auth): `GET /api/scheduling/event-types`, `GET /api/scheduling/event-types/{id}/booking-fields`, `PUT /api/scheduling/event-types/{id}/booking-fields`. Error map mirrors event-admin's existing proxy routes.

**`event-admin-frontend`:**
- A new "Поля записи" (Booking fields) surface: list event types → pick one → edit its fields. The editor lets the admin add/remove/reorder fields and set `field_type`, `label`, `placeholder`, `required`, and (for option types) `options`. Save = `PUT` the ordered list. Uses the design-system components/classes (`Icon`, `Switch`, `.field`, `.card`, table styles).

## Error handling

- **event-scheduling:** invalid `PUT` items → `422` (per-item, field-keyed); booking-create with invalid/missing answers → `422`; unknown event type → `404`. Option/type mismatches are explicit messages.
- **BFF:** forwards event-scheduling's `422`/`404` as `{"detail": …}`; never trusts client-supplied answers as valid.
- **booker-frontend:** client-side required/option validation before submit; on a server `422` shows the field/banner error and keeps the form filled.
- **admin-frontend:** validation errors from `PUT` surfaced inline in the editor.

## Testing

- **event-scheduling:** unit + DB tests for `booking_field` CRUD (replace-all, key derivation/dedupe, option validation), the extended public event-type read, and booking-create answer validation (required, option membership, checkbox subset, boolean, unknown key → 422) + snapshot storage. Migration up/down.
- **event-booker:** BFF forwards `booking_fields` on the event-type read and `answers` on create; surfaces upstream `422`.
- **event-booker-frontend:** `GuestForm` renders each field type; required/option client validation; `answers` included in the submitted body (vitest + happy-dom).
- **event-admin:** scheduling-proxy client + routes (mocked upstream).
- **event-admin-frontend:** the editor renders/edits/reorders fields and `PUT`s the list (vitest).

## Non-goals (deferred)

- **Viewing** answers in admin / organizer ЛК / notification emails. MVP **stores** answers on the booking and includes them in the `booking.lifecycle` payload; the display + notification UIs are a follow-up slice (they can consume the stored snapshot / event payload without re-collecting).
- Conditional / branching fields (show field B only if A = x).
- Validation beyond required + option-membership (no regex, min/max length, number ranges) in MVP.
- File-upload fields; `phone`/`number`/`date` types (easy future additions).
- Making name / email configurable (they stay built-in; the BFF resolves the client by email).
- Granular per-field CRUD endpoints (replace-all `PUT` is the MVP write model).

## Rollout

Backend-first: Phase 1 (event-scheduling) is independently mergeable and testable via API; Phase 2 (BFF + booker-frontend) delivers the guest-facing feature on top; Phase 3 (event-admin proxy + admin-frontend editor) replaces API/DB configuration with a UI. Each phase is its own set of tasks in the plan. Builds on the shipped `events-design-system` (both frontends consume it) and the compact-calendar work.
