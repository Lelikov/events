# Booking Fields — Phase 2 (public rendering) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose each event type's configured booking fields through the public `event-booker` BFF and render them dynamically on the Booker's guest form (with client-side validation), submitting the guest's answers with the booking — plus the input-size hardening the Phase-1 review required before public exposure.

**Architecture:** Builds on Phase 1 (event-scheduling already stores/validates/exposes fields+answers). Task 1 adds size bounds to event-scheduling's authoritative validators. Task 2 has the `event-booker` BFF carry `booking_fields` on the single event-type read and forward `answers` on booking-create (event-scheduling stays the authoritative validator; the BFF forwards). Tasks 3–4 add the booker-frontend: pure answer helpers, then a dynamic `GuestForm` that renders a control per field type and threads answers through `BookingFlowPage`.

**Tech Stack:** Python 3.14 / FastAPI / Dishka / httpx (event-scheduling, event-booker); React 19 / Vite / TypeScript / vitest+happy-dom (event-booker-frontend); `events-design-system` form classes.

## Global Constraints

- **Field types (6):** `text`, `textarea`, `select`, `radio`, `checkbox`, `boolean`. `select`/`radio`/`checkbox` carry `options` (`{value,label}[]`). Value shapes: text/textarea → string; select/radio → one option value; checkbox → string[] (subset of option values); boolean → bool.
- **Answer wire shape:** `answers: [{key: str, value: str | str[] | bool}]`. event-scheduling stores the snapshot `[{key,label,type,value}]`; the BFF forwards answers verbatim and never re-validates (event-scheduling is authoritative — a `422` from it surfaces as the BFF's `{"detail": …}`).
- **Size bounds (Task 1, chosen — event-scheduling authoritative):** ≤ **50** fields per event type; ≤ **100** options per option field; `label` ≤ **200** chars; `placeholder` ≤ **500** chars; each option `value`/`label` ≤ **200** chars; `text`/`textarea` answer value ≤ **10000** chars. Violations → `ValidationError` (422).
- **Name + email stay built-in** on the guest form; dynamic fields render after them. The BFF still resolves guest→client by email.
- **Branches/commits:** work on `feat/booking-fields-p2` off `feat/booking-fields-p1`. `event-scheduling`, `event-booker`, and `event-booker-frontend` are all **root-tracked** → commit from repo root `/Users/alexandrlelikov/PycharmProjects/events`, staging the relevant `event-*/…` paths.
- **event-scheduling tests:** `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5602/event_scheduling'`, `SCHEDULING_API_KEY` test key `test-scheduling-key`.
- **Conventions:** Python — no elif/avoid else, Ruff 120, frozen DTOs, Pydantic only in `schemas/`, Protocol interfaces, raw `:param` SQL. TS — `verbatimModuleSyntax` (`import { X, type Y }`, `.ts`/`.tsx` extensions), no `else`/`elif`, plain CSS, Russian UI copy, vitest+happy-dom with `createRoot`+`act`.

---

### Task 1: Size-bound hardening (event-scheduling)

**Files:**
- Modify: `event-scheduling/event_scheduling/booking_fields/domain.py`
- Modify: `event-scheduling/tests/test_booking_fields_domain.py`

**Interfaces:**
- Consumes: existing `validate_field_items`, `validate_and_snapshot`, `_validate_one` (Phase 1).
- Produces: the same functions, now enforcing the size bounds; new module constants `MAX_FIELDS`, `MAX_OPTIONS`, `MAX_LABEL_LEN`, `MAX_PLACEHOLDER_LEN`, `MAX_OPTION_LEN`, `MAX_TEXT_ANSWER_LEN`.

- [ ] **Step 1: Add failing tests** to `tests/test_booking_fields_domain.py`:

```python
def test_validate_field_items_enforces_size_bounds():
    from event_scheduling.booking_fields.domain import MAX_FIELDS, MAX_OPTIONS
    # too many fields
    many = [UpsertBookingFieldDTO("text", f"F{i}", None, False, []) for i in range(MAX_FIELDS + 1)]
    with pytest.raises(ValidationError):
        validate_field_items(many)
    # too many options
    opts = [OptionDTO(value=f"v{i}", label=f"L{i}") for i in range(MAX_OPTIONS + 1)]
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("select", "Pick", None, False, opts)])
    # label too long
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "x" * 201, None, False, [])])
    # placeholder too long
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "ok", "p" * 501, False, [])])
    # option value too long
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("radio", "R", None, False, [OptionDTO("v" * 201, "L")])])


def test_validate_and_snapshot_caps_text_length():
    fields = [_field("note", "textarea", required=False)]
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("note", "x" * 10001)])
    # at the cap is fine
    snap = validate_and_snapshot(fields, [AnswerDTO("note", "x" * 10000)])
    assert snap[0].value == "x" * 10000
```

- [ ] **Step 2: Run — expect failure.** `cd event-scheduling && TEST_POSTGRES_DSN=... uv run pytest tests/test_booking_fields_domain.py -k size_bounds -o addopts='' -q` and the caps test — FAIL (bounds not enforced yet). (Run without `-k` too if the harness needs its default DSN.)

- [ ] **Step 3: Add the constants + enforce in `domain.py`.** Near the top, after `OPTION_TYPES`:

```python
MAX_FIELDS = 50
MAX_OPTIONS = 100
MAX_LABEL_LEN = 200
MAX_PLACEHOLDER_LEN = 500
MAX_OPTION_LEN = 200
MAX_TEXT_ANSWER_LEN = 10000
```

In `validate_field_items`, add a count check at the top and per-item length/option-count checks:

```python
def validate_field_items(items: list[UpsertBookingFieldDTO]) -> None:
    if len(items) > MAX_FIELDS:
        raise ValidationError(f"too many booking fields (max {MAX_FIELDS})")
    for it in items:
        if not it.label.strip():
            raise ValidationError("booking field label must not be empty")
        if len(it.label) > MAX_LABEL_LEN:
            raise ValidationError(f"label too long (max {MAX_LABEL_LEN})")
        if it.placeholder is not None and len(it.placeholder) > MAX_PLACEHOLDER_LEN:
            raise ValidationError(f"placeholder too long (max {MAX_PLACEHOLDER_LEN})")
        if it.field_type not in FIELD_TYPES:
            raise ValidationError(f"unknown field_type {it.field_type!r}")
        is_option = it.field_type in OPTION_TYPES
        if is_option and len(it.options) < 1:
            raise ValidationError(f"field {it.label!r} of type {it.field_type} needs at least one option")
        if is_option and len(it.options) > MAX_OPTIONS:
            raise ValidationError(f"field {it.label!r} has too many options (max {MAX_OPTIONS})")
        if not is_option and it.options:
            raise ValidationError(f"field {it.label!r} of type {it.field_type} must not have options")
        values = [o.value for o in it.options]
        if is_option and (any(not v.strip() for v in values) or len(set(values)) != len(values)):
            raise ValidationError(f"field {it.label!r} has empty or duplicate option values")
        if is_option and any(len(o.value) > MAX_OPTION_LEN or len(o.label) > MAX_OPTION_LEN for o in it.options):
            raise ValidationError(f"field {it.label!r} has an option value/label that is too long (max {MAX_OPTION_LEN})")
```

In `_validate_one`, cap text length in the text/textarea branch:

```python
    if ftype in ("text", "textarea"):
        if not isinstance(value, str):
            raise ValidationError(f"field {field.field_key!r} expects text")
        if len(value) > MAX_TEXT_ANSWER_LEN:
            raise ValidationError(f"field {field.field_key!r} answer is too long (max {MAX_TEXT_ANSWER_LEN})")
        return value
```

- [ ] **Step 4: Run tests + Ruff.** `cd event-scheduling && TEST_POSTGRES_DSN=... uv run pytest tests/test_booking_fields_domain.py -q && uv run ruff check event_scheduling/booking_fields && uv run ruff format --check event_scheduling/booking_fields` → all pass, clean.

- [ ] **Step 5: Commit.**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/booking_fields/domain.py event-scheduling/tests/test_booking_fields_domain.py
git commit -m "feat(scheduling): size bounds on booking fields + text answers (pre-public hardening)"
```

---

### Task 2: event-booker BFF — surface `booking_fields`, forward `answers`

**Files:**
- Modify: `event-booker/event_booker/dto.py` (BookingFieldDTO/OptionDTO + `EventTypeDTO.booking_fields`)
- Modify: `event-booker/event_booker/interfaces/clients.py` (`create_booking` gains `field_answers`)
- Modify: `event-booker/event_booker/adapters/scheduling_client.py` (parse fields on read; send answers on create)
- Modify: `event-booker/event_booker/schemas/public.py` (`EventTypeModel.booking_fields`, `CreateBookingPublicRequest.answers`)
- Modify: `event-booker/event_booker/services/guest_booking.py` (`book(..., answers)`)
- Modify: `event-booker/event_booker/routers/public.py` (thread answers)
- Test: `event-booker/tests/test_booking_fields_bff.py`

**Interfaces:**
- Produces:
  - `dto.OptionDTO(value: str, label: str)`, `dto.BookingFieldDTO(field_key, field_type, label, placeholder, required, options: list[OptionDTO])`, `EventTypeDTO.booking_fields: list[BookingFieldDTO]` (default `field(default_factory=list)`).
  - `dto.AnswerDTO(key: str, value: str | list[str] | bool)`.
  - `ISchedulingClient.create_booking(..., field_answers: list[AnswerDTO])`.
  - `GuestBookingService.book(event_type_id, name, email, start_time, time_zone, answers: list[AnswerDTO])`.
  - Public schemas `OptionModel`, `BookingFieldModel`, `AnswerModel`; `EventTypeModel.booking_fields`, `CreateBookingPublicRequest.answers`.

- [ ] **Step 1: Write the failing BFF test `tests/test_booking_fields_bff.py`** (mock event-scheduling with `httpx.MockTransport`; mirror the existing scheduling-client tests' style — read `event-booker/tests/` for the transport-mock helper):

```python
import httpx
import pytest
from uuid import uuid4
from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.dto import AnswerDTO


@pytest.mark.asyncio
async def test_get_event_type_parses_booking_fields():
    et_id = uuid4()
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": str(et_id), "slug": "s", "title": "T", "duration_minutes": 30,
            "booking_fields": [{"field_key": "reason", "field_type": "textarea", "label": "Reason",
                                 "placeholder": None, "required": True, "options": [], "position": 0}],
        })
    c = SchedulingClient("http://sched", "k", transport=httpx.MockTransport(handler))
    et = await c.get_event_type(et_id)
    assert [f.field_key for f in et.booking_fields] == ["reason"]
    assert et.booking_fields[0].required is True


@pytest.mark.asyncio
async def test_create_booking_forwards_answers():
    sent = {}
    def handler(req: httpx.Request) -> httpx.Response:
        import json
        sent.update(json.loads(req.content))
        return httpx.Response(201, json={"id": str(uuid4()), "start_time": "2026-10-01T09:00:00Z",
                                          "end_time": "2026-10-01T09:30:00Z", "status": "confirmed"})
    c = SchedulingClient("http://sched", "k", transport=httpx.MockTransport(handler))
    await c.create_booking(uuid4(), uuid4(), __import__("datetime").datetime(2026, 10, 1, 9, 0), "UTC",
                           field_answers=[AnswerDTO("reason", "help"), AnswerDTO("topics", ["a", "b"])])
    assert sent["field_answers"] == [{"key": "reason", "value": "help"}, {"key": "topics", "value": ["a", "b"]}]
```

- [ ] **Step 2: Run — expect failure.** `cd event-booker && uv run pytest tests/test_booking_fields_bff.py -q` → FAIL.

- [ ] **Step 3: DTOs (`dto.py`).** Add (import `field` from dataclasses):

```python
@dataclass(frozen=True)
class OptionDTO:
    value: str
    label: str


@dataclass(frozen=True)
class BookingFieldDTO:
    field_key: str
    field_type: str
    label: str
    placeholder: str | None
    required: bool
    options: list[OptionDTO]


@dataclass(frozen=True)
class AnswerDTO:
    key: str
    value: str | list[str] | bool
```

and add `booking_fields: list[BookingFieldDTO] = field(default_factory=list)` to `EventTypeDTO` (keep it last).

- [ ] **Step 4: Parse fields + send answers (`scheduling_client.py`).** In `_to_event_type`, parse `booking_fields`:

```python
    @staticmethod
    def _to_event_type(item: dict) -> EventTypeDTO:
        fields = [
            BookingFieldDTO(
                field_key=f["field_key"], field_type=f["field_type"], label=f["label"],
                placeholder=f.get("placeholder"), required=f["required"],
                options=[OptionDTO(value=o["value"], label=o["label"]) for o in (f.get("options") or [])],
            )
            for f in item.get("booking_fields", [])
        ]
        return EventTypeDTO(id=UUID(item["id"]), slug=item["slug"], title=item["title"],
                            duration_minutes=item["duration_minutes"], booking_fields=fields)
```

(import `BookingFieldDTO, OptionDTO`.) In `create_booking`, add `field_answers: list[AnswerDTO]` param and include it in the body:

```python
        body = {
            "event_type_id": str(event_type_id),
            "client_user_id": str(client_user_id),
            "start_time": start_time.isoformat(),
            "attendee_time_zone": attendee_time_zone,
            "field_answers": [{"key": a.key, "value": a.value} for a in field_answers],
        }
```

(import `AnswerDTO`.) Update `ISchedulingClient.create_booking` in `interfaces/clients.py` to the new signature.

- [ ] **Step 5: Public schemas (`schemas/public.py`).** Add `OptionModel`, `BookingFieldModel(+from_dto)`, and `booking_fields: list[BookingFieldModel] = []` to `EventTypeModel` (map in `from_dto`); add `AnswerModel(key: str, value: str | list[str] | bool)` and `answers: list[AnswerModel] = []` to `CreateBookingPublicRequest`.

- [ ] **Step 6: Service + router.** In `guest_booking.py`, `book(...)` gains `answers: list[AnswerDTO]` and passes `field_answers=answers` to `create_booking`. In `routers/public.py` `create_booking`, pass `answers=[AnswerDTO(key=a.key, value=a.value) for a in body.answers]` into `service.book(...)` (import `AnswerDTO`).

- [ ] **Step 7: Run the BFF suite + Ruff.** `cd event-booker && uv run pytest -q && uv run ruff check . && uv run ruff format --check .` → the new tests + full existing BFF suite green (defaults keep the event-type list/other paths working), Ruff clean.

- [ ] **Step 8: Commit.**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/event_booker/ event-booker/tests/test_booking_fields_bff.py
git commit -m "feat(booker): BFF surfaces booking_fields on event-type read + forwards answers"
```

---

### Task 3: booker-frontend — types + api + pure answer helpers

**Files:**
- Modify: `event-booker-frontend/src/modules/booking/types.ts`
- Create: `event-booker-frontend/src/modules/booking/answers.ts`
- Test: `event-booker-frontend/src/modules/booking/answers.test.ts`

**Interfaces:**
- Produces (types.ts):
  - `type FieldOption = { value: string; label: string }`
  - `type BookingField = { field_key: string; field_type: 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'boolean'; label: string; placeholder: string | null; required: boolean; options: FieldOption[] }`
  - `EventType` gains `booking_fields?: BookingField[]`
  - `type Answer = { key: string; value: string | string[] | boolean }`
  - `CreateBookingBody` gains `answers: Answer[]`
- Produces (answers.ts):
  - `type AnswerValues = Record<string, string | string[] | boolean>`
  - `initialValues(fields: BookingField[]): AnswerValues` — `''` for text/textarea/select/radio, `[]` for checkbox, `false` for boolean.
  - `validateAnswers(fields: BookingField[], values: AnswerValues): string | null` — first required-but-empty field's label → error message, else `null` (mirrors the server: text/select/radio empty string, empty checkbox array, boolean not-true are "empty" when required).
  - `buildAnswers(fields: BookingField[], values: AnswerValues): Answer[]` — one `{key,value}` per field the user actually filled (non-empty string, non-empty array, or a boolean that is `true`); omit empty optional fields.

- [ ] **Step 1: Write the failing test `answers.test.ts`**:

```ts
import { describe, expect, it } from 'vitest'
import { buildAnswers, initialValues, validateAnswers } from './answers.ts'
import type { BookingField } from './types.ts'

const f = (over: Partial<BookingField>): BookingField => ({
  field_key: 'k', field_type: 'text', label: 'L', placeholder: null, required: false, options: [], ...over,
})

describe('answers helpers', () => {
  it('initialValues seeds per type', () => {
    const v = initialValues([f({ field_key: 't', field_type: 'text' }), f({ field_key: 'c', field_type: 'checkbox' }), f({ field_key: 'b', field_type: 'boolean' })])
    expect(v).toEqual({ t: '', c: [], b: false })
  })

  it('validateAnswers flags the first required-empty field', () => {
    const fields = [f({ field_key: 'reason', field_type: 'textarea', label: 'Причина', required: true })]
    expect(validateAnswers(fields, { reason: '' })).toContain('Причина')
    expect(validateAnswers(fields, { reason: 'ok' })).toBeNull()
  })

  it('buildAnswers omits empty optional fields and includes filled ones', () => {
    const fields = [
      f({ field_key: 'reason', field_type: 'textarea' }),
      f({ field_key: 'topics', field_type: 'checkbox', options: [{ value: 'a', label: 'A' }] }),
      f({ field_key: 'agree', field_type: 'boolean' }),
    ]
    const out = buildAnswers(fields, { reason: '', topics: ['a'], agree: true })
    expect(out).toEqual([{ key: 'topics', value: ['a'] }, { key: 'agree', value: true }])
  })
})
```

- [ ] **Step 2: Run — expect failure** (`cd event-booker-frontend && npm test -- answers.test.ts` → FAIL).

- [ ] **Step 3: Extend `types.ts`** with the `FieldOption`/`BookingField`/`Answer` types, `EventType.booking_fields?`, and `CreateBookingBody.answers` (exact shapes in Interfaces above).

- [ ] **Step 4: Implement `answers.ts`**:

```ts
import type { Answer, AnswerValues, BookingField } from './types.ts'

export function initialValues(fields: BookingField[]): AnswerValues {
  const v: AnswerValues = {}
  for (const f of fields) {
    if (f.field_type === 'checkbox') v[f.field_key] = []
    else if (f.field_type === 'boolean') v[f.field_key] = false
    else v[f.field_key] = ''
  }
  return v
}

function isEmpty(field: BookingField, value: string | string[] | boolean): boolean {
  if (field.field_type === 'checkbox') return !Array.isArray(value) || value.length === 0
  if (field.field_type === 'boolean') return value !== true
  return typeof value === 'string' && value.trim() === ''
}

export function validateAnswers(fields: BookingField[], values: AnswerValues): string | null {
  for (const f of fields) {
    if (f.required && isEmpty(f, values[f.field_key])) return `Заполните поле «${f.label}»`
  }
  return null
}

export function buildAnswers(fields: BookingField[], values: AnswerValues): Answer[] {
  const out: Answer[] = []
  for (const f of fields) {
    const value = values[f.field_key]
    if (!isEmpty(f, value)) out.push({ key: f.field_key, value })
  }
  return out
}
```

Put `type AnswerValues = Record<string, string | string[] | boolean>` in `answers.ts` and export it (or in types.ts — keep the import in the test working: the test imports helpers from `answers.ts` and `BookingField` from `types.ts`, so `AnswerValues` living in either is fine as long as `answers.ts` re-exports what it uses).

- [ ] **Step 5: Run + build + lint.** `cd event-booker-frontend && npm test -- answers.test.ts && npm run build && npm run lint` → pass, clean.

- [ ] **Step 6: Commit.**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/types.ts \
        event-booker-frontend/src/modules/booking/answers.ts \
        event-booker-frontend/src/modules/booking/answers.test.ts
git commit -m "feat(booker-fe): booking-field types + pure answer validate/build helpers"
```

---

### Task 4: booker-frontend — dynamic `GuestForm` + `BookingFlowPage` wiring

**Files:**
- Modify: `event-booker-frontend/src/modules/booking/GuestForm.tsx`
- Modify: `event-booker-frontend/src/modules/booking/BookingFlowPage.tsx`
- Modify: `event-booker-frontend/src/App.css` (dynamic-field styling, minimal)
- Test: `event-booker-frontend/src/modules/booking/GuestForm.test.tsx`

**Interfaces:**
- Consumes: `BookingField`, `Answer` (types.ts); `initialValues`, `validateAnswers`, `buildAnswers` (answers.ts, Task 3); `createBooking` (bookerApi).
- Produces: `GuestForm` props `{ fields: BookingField[]; onSubmit: (name: string, email: string, answers: Answer[]) => void; onBack; submitError?; submitting? }`.

- [ ] **Step 1: Write the failing test `GuestForm.test.tsx`** (createRoot + act, mirroring the existing booker tests):

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { GuestForm } from './GuestForm.tsx'
import type { BookingField } from './types.ts'

let container: HTMLDivElement
let root: Root

const field = (o: Partial<BookingField>): BookingField => ({
  field_key: 'k', field_type: 'text', label: 'L', placeholder: null, required: false, options: [], ...o,
})

async function mount(fields: BookingField[], onSubmit = vi.fn()) {
  container = document.createElement('div'); document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<GuestForm fields={fields} onSubmit={onSubmit} onBack={vi.fn()} />))
  return { onSubmit }
}
afterEach(() => { act(() => root.unmount()); container.remove(); vi.clearAllMocks() })

function setInput(sel: string, value: string) {
  const el = container.querySelector(sel) as HTMLInputElement | HTMLTextAreaElement
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('GuestForm dynamic fields', () => {
  it('renders a control per field type and blocks submit on a missing required field', async () => {
    const fields = [
      field({ field_key: 'reason', field_type: 'textarea', label: 'Причина', required: true }),
      field({ field_key: 'topic', field_type: 'select', label: 'Тема', options: [{ value: 'a', label: 'A' }] }),
      field({ field_key: 'agree', field_type: 'boolean', label: 'Согласие' }),
    ]
    const { onSubmit } = await mount(fields)
    expect(container.querySelector('textarea')).toBeTruthy()
    expect(container.querySelector('select')).toBeTruthy()
    // fill name+email but not the required 'reason' → submit blocked
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'ada@x.io')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Причина')
  })

  it('submits name, email and answers when valid', async () => {
    const fields = [field({ field_key: 'reason', field_type: 'text', label: 'Причина', required: true })]
    const { onSubmit } = await mount(fields)
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'ada@x.io')
    setInput('input[name="field-reason"]', 'help')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).toHaveBeenCalledWith('Ada', 'ada@x.io', [{ key: 'reason', value: 'help' }])
  })
})
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Rewrite `GuestForm.tsx`** to render name+email (with `name="name"`/`name="email"`) then a control per field, holding answer values in state, validating on submit via `validateAnswers`, and emitting `buildAnswers` output:

```tsx
import { useState, type FormEvent } from 'react'
import type { Answer, AnswerValues, BookingField } from './types.ts'
import { buildAnswers, initialValues, validateAnswers } from './answers.ts'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Props = {
  fields: BookingField[]
  onSubmit: (name: string, email: string, answers: Answer[]) => void
  onBack: () => void
  submitError?: string | null
  submitting?: boolean
}

export function GuestForm({ fields, onSubmit, onBack, submitError, submitting }: Props) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [values, setValues] = useState<AnswerValues>(() => initialValues(fields))
  const [error, setError] = useState<string | null>(null)

  function setValue(key: string, value: string | string[] | boolean) {
    setValues((v) => ({ ...v, [key]: value }))
  }

  function toggleCheckbox(key: string, optionValue: string, checked: boolean) {
    setValues((v) => {
      const current = Array.isArray(v[key]) ? (v[key] as string[]) : []
      const next = checked ? [...current, optionValue] : current.filter((x) => x !== optionValue)
      return { ...v, [key]: next }
    })
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (name.trim() === '') return setError('Введите имя')
    if (!EMAIL_RE.test(email)) return setError('Введите корректный email')
    const answerError = validateAnswers(fields, values)
    if (answerError) return setError(answerError)
    setError(null)
    onSubmit(name.trim(), email.trim(), buildAnswers(fields, values))
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field">
        <span>Имя</span>
        <input name="name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Email</span>
        <input name="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>

      {fields.map((f) => (
        <DynamicField key={f.field_key} field={f} value={values[f.field_key]} onChange={setValue} onToggle={toggleCheckbox} />
      ))}

      {error && <p className="field-error">{error}</p>}
      {submitError && <p className="banner-error">{submitError}</p>}
      <div className="inline-actions">
        <button type="button" onClick={onBack} disabled={submitting}>← Назад</button>
        <button type="submit" disabled={submitting}>{submitting ? 'Бронируем…' : 'Забронировать'}</button>
      </div>
    </form>
  )
}

type FieldProps = {
  field: BookingField
  value: string | string[] | boolean
  onChange: (key: string, value: string | string[] | boolean) => void
  onToggle: (key: string, optionValue: string, checked: boolean) => void
}

function DynamicField({ field, value, onChange, onToggle }: FieldProps) {
  const label = (
    <span>
      {field.label}
      {field.required ? ' *' : ''}
    </span>
  )
  const name = `field-${field.field_key}`

  if (field.field_type === 'textarea') {
    return (
      <label className="field">
        {label}
        <textarea name={name} placeholder={field.placeholder ?? ''} value={value as string}
                  onChange={(e) => onChange(field.field_key, e.target.value)} />
      </label>
    )
  }
  if (field.field_type === 'select') {
    return (
      <label className="field">
        {label}
        <select name={name} value={value as string} onChange={(e) => onChange(field.field_key, e.target.value)}>
          <option value="">—</option>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
    )
  }
  if (field.field_type === 'radio') {
    return (
      <div className="field">
        {label}
        <div className="radio-group">
          {field.options.map((o) => (
            <label key={o.value} className="radio-option">
              <input type="radio" name={name} value={o.value} checked={value === o.value}
                     onChange={() => onChange(field.field_key, o.value)} />
              {o.label}
            </label>
          ))}
        </div>
      </div>
    )
  }
  if (field.field_type === 'checkbox') {
    const list = Array.isArray(value) ? value : []
    return (
      <div className="field">
        {label}
        <div className="checkbox-group">
          {field.options.map((o) => (
            <label key={o.value} className="checkbox-option">
              <input type="checkbox" checked={list.includes(o.value)}
                     onChange={(e) => onToggle(field.field_key, o.value, e.target.checked)} />
              {o.label}
            </label>
          ))}
        </div>
      </div>
    )
  }
  if (field.field_type === 'boolean') {
    return (
      <label className="checkbox-option">
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(field.field_key, e.target.checked)} />
        {field.label}{field.required ? ' *' : ''}
      </label>
    )
  }
  return (
    <label className="field">
      {label}
      <input name={name} placeholder={field.placeholder ?? ''} value={value as string}
             onChange={(e) => onChange(field.field_key, e.target.value)} />
    </label>
  )
}
```

> Note: `return setError(...)` returns `undefined` (setError returns void) which satisfies the `void` handler — this is the early-return, no-`else` style. If the linter objects to returning a call result, split into `{ setError(...); return }`.

- [ ] **Step 4: Wire `BookingFlowPage.tsx`.** Pass `fields={eventType?.booking_fields ?? []}` to `GuestForm`, and change `handleSubmit` to accept answers and include them in `createBooking`:

```tsx
  async function handleSubmit(name: string, email: string, answers: Answer[]) {
    if (selected === null) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await createBooking({
        event_type_id: eventTypeId, name, email, start_time: selected, time_zone: timeZone, answers,
      })
      setConfirmation(result); setStep('done')
    } catch (err) {
      // ...unchanged 409/422/other handling...
    } finally {
      setSubmitting(false)
    }
  }
```

Update the `<GuestForm .../>` usage in the `details` step to `fields={eventType?.booking_fields ?? []}` and the new `onSubmit={handleSubmit}`. Import `type { Answer }`. Keep the existing 409/422/generic error branches exactly.

- [ ] **Step 5: Minimal CSS** in `event-booker-frontend/src/App.css` for the new groups:

```css
.radio-group, .checkbox-group { display: grid; gap: 6px; margin-top: 4px; }
.radio-option, .checkbox-option { display: flex; align-items: center; gap: 8px; font-size: 14px; }
```

- [ ] **Step 6: Run the booker-frontend suite + build + lint.** `cd event-booker-frontend && npm test && npm run build && npm run lint` → the new `GuestForm` tests + the existing suite (SlotPicker/BookingFlowPage/etc.) green. If `BookingFlowPage.test.tsx` drove the old `GuestForm` (name/email only), update it to the new `onSubmit(name,email,answers)` shape, preserving intent.

- [ ] **Step 7: Commit.**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/GuestForm.tsx \
        event-booker-frontend/src/modules/booking/GuestForm.test.tsx \
        event-booker-frontend/src/modules/booking/BookingFlowPage.tsx \
        event-booker-frontend/src/App.css
git commit -m "feat(booker-fe): dynamic booking-field form + thread answers into the booking"
```

---

## Notes for the executor

- **Branch:** create `feat/booking-fields-p2` off `feat/booking-fields-p1` (so event-scheduling's Phase-1 changes are present) before Task 1.
- **Task order:** 1 (scheduling hardening) → 2 (BFF) → 3 (frontend helpers) → 4 (frontend form). 2 depends on Phase-1's exposed `booking_fields`/answers (already merged into the branch); 4 depends on 3's helpers/types.
- **Authoritative validation stays server-side.** The frontend validation and BFF are UX/forwarding only; event-scheduling (Task 1 + Phase 1) is the source of truth. Do not duplicate the size caps in the BFF.
- **`BookingFlowPage.test.tsx`:** the calendar-slice left it asserting on the slot flow; Task 4 only needs it green with the new `GuestForm` signature — adjust minimally, don't rewrite its intent.
- **Manual smoke (optional, after Task 4):** with event-booker on :8005 and event-scheduling on :8004, configure a field via `PUT /api/v1/event-types/{id}/booking-fields`, run the booker-frontend dev server, and confirm the field renders on the guest step and the answer submits.
- **Phase 3 (event-admin proxy + admin-frontend editor)** is the next and final plan, written after this merges.
