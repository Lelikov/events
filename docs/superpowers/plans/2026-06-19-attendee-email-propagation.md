# Проброс смены email клиента в cal.com Attendee — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** При смене email клиента в бронировании через админку новый email записывается и в cal.com `"Attendee"` (в строке этого бронирования) — без зацикливания через триггер event-db-sync.

**Architecture:** event-admin кладёт `booking_uid` в событие `user.email.change_requested`; новая fan-out очередь `events.user.email.booking` доставляет копию события в event-booking; event-booking в одной транзакции выставляет `SET LOCAL app.sync_suppress='on'` и делает `UPDATE "Attendee"` по `Booking.uid → Booking.id → Attendee.bookingId` (match `old_email`); триггерная функция event-db-sync уважает `app.sync_suppress` и не шлёт `pg_notify`.

**Tech Stack:** Python 3.14, FastAPI, Dishka, FastStream, SQLAlchemy raw `text()` SQL, Pydantic v2, asyncpg/pg триггеры, React+TS+Vite (Vitest+happy-dom). Репозитории: `event-schemas`, `event-db-sync`, `event-booking`, `event-admin`, `event-admin-frontend`.

**Spec:** `docs/superpowers/specs/2026-06-19-attendee-email-propagation-design.md`

---

## Conventions (каждая задача обязана соблюдать)

- **Коммиты в свой репозиторий.** Перед `git add`/`commit` — `cd` в нужный репо. Каждый репо — отдельный git.
- **Сообщения коммитов** заканчиваются трейлером:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **НЕ пушить** (push тега v0.5.0 — отдельный user-gated шаг, Task 11).
- **Без `elif`; избегать `else`** — ранние возвраты, guard-clause, mapping-dict.
- **Ruff** line-length 120: `uv run ruff check --fix . && uv run ruff format .` перед коммитом (для фронта — `npm run lint` если есть; иначе ничего).
- **TDD**: пишем падающий тест → red → реализация → green → commit.
- **event-schemas во время разработки:** event-booking использует временно `event-schemas = { path = "../event-schemas", editable = true }` чтобы тестировать против невыпущенной v0.5.0. Task 11 флипает на `tag = "v0.5.0"` после пуша тега.
- **Ветки:** где уже есть `feat/event-db-sync` (event-schemas) — продолжаем на ней. Где репо на `main` (event-booking, event-admin, event-admin-frontend) — создать ветку `feat/attendee-email-sync` перед первым коммитом.

## Канонические имена (использовать ДОСЛОВНО)

| Сущность | Значение |
|---|---|
| Новое поле payload | `UserEmailChangeRequestedPayload.booking_uid: str \| None = None` |
| Новая очередь | `USER_EMAIL_BOOKING_QUEUE`, name `"events.user.email.booking"`, binding `RoutingKey.USER_EMAIL`, consumer `"event-booking"` |
| GUC-флаг подавления | `app.sync_suppress` (значение `'on'`) |
| Метод адаптера cal.com | `update_attendee_email(booking_uid, old_email, new_email) -> None` |
| Метод консьюмера | `register_user_email(broker, exchange, queue_spec)` + `handle_user_email_change(data)` |
| Версия / тег event-schemas | `0.5.0` / `v0.5.0` |
| Тип события (без изменений) | `EventType.USER_EMAIL_CHANGE_REQUESTED = "user.email.change_requested"` |

---

# PHASE 1 — event-schemas (репо `event-schemas/`, ветка `feat/event-db-sync`)

### Task 1: Добавить `booking_uid` в payload + fan-out очередь `USER_EMAIL_BOOKING_QUEUE`

**Files:**
- Modify: `event_schemas/user.py` (класс `UserEmailChangeRequestedPayload`, строки ~26-43)
- Modify: `event_schemas/queues.py` (рядом с `USER_EMAIL_QUEUE` и в `ALL_QUEUES`)
- Modify (append): `tests/test_queues.py`, `tests/test_user_payloads.py`

- [ ] **Step 1: Падающие тесты** — добавить в `tests/test_user_payloads.py`:

```python
def test_user_email_change_accepts_booking_uid() -> None:
    from event_schemas.user import UserEmailChangeRequestedPayload

    p = UserEmailChangeRequestedPayload(
        user_id="550e8400-e29b-41d4-a716-446655440001",
        old_email="old@example.com",
        new_email="new@example.com",
        requested_by="admin@company.com",
        booking_uid="book-123",
    )
    assert p.booking_uid == "book-123"


def test_user_email_change_booking_uid_optional() -> None:
    from event_schemas.user import UserEmailChangeRequestedPayload

    p = UserEmailChangeRequestedPayload(
        user_id="550e8400-e29b-41d4-a716-446655440001",
        old_email="old@example.com",
        new_email="new@example.com",
        requested_by="admin@company.com",
    )
    assert p.booking_uid is None
```

И в `tests/test_queues.py`:

```python
def test_user_email_booking_queue_is_fanout() -> None:
    from event_schemas.queues import (
        ALL_QUEUES,
        SAVER_QUEUES,
        USER_EMAIL_BOOKING_QUEUE,
        USER_EMAIL_QUEUE,
        RoutingKey,
    )

    # fan-out: две очереди на один routing key, разные имена/consumer'ы
    assert USER_EMAIL_BOOKING_QUEUE.name == "events.user.email.booking"
    assert USER_EMAIL_BOOKING_QUEUE.binding == RoutingKey.USER_EMAIL
    assert USER_EMAIL_BOOKING_QUEUE.binding == USER_EMAIL_QUEUE.binding
    assert USER_EMAIL_BOOKING_QUEUE.consumer == "event-booking"
    assert USER_EMAIL_BOOKING_QUEUE in ALL_QUEUES
    assert USER_EMAIL_BOOKING_QUEUE not in SAVER_QUEUES
```

- [ ] **Step 2: Red**

Run: `cd event-schemas && uv run pytest tests/test_user_payloads.py tests/test_queues.py -v`
Expected: FAIL (`booking_uid` нет в модели; `USER_EMAIL_BOOKING_QUEUE` не импортируется).

- [ ] **Step 3: Реализация payload** — в `event_schemas/user.py`, в класс `UserEmailChangeRequestedPayload` добавить поле после `requested_by` и ключ в `example`:

```python
    requested_by: str = Field(..., description="Admin email who requested the change")
    booking_uid: str | None = Field(None, description="Booking UID whose Attendee email is also updated; null if N/A")

    model_config = {
        "json_schema_extra": {
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440001",
                "old_email": "old@example.com",
                "new_email": "new@example.com",
                "requested_by": "admin@company.com",
                "booking_uid": "book-123",
            }
        }
    }
```

- [ ] **Step 4: Реализация очереди** — в `event_schemas/queues.py` объявить новую очередь сразу после блока `USER_EMAIL_QUEUE = QueueSpec(...)`:

```python
USER_EMAIL_BOOKING_QUEUE = QueueSpec(
    name="events.user.email.booking",
    binding=RoutingKey.USER_EMAIL,
    consumer="event-booking",
)
```

и добавить `USER_EMAIL_BOOKING_QUEUE,` в кортеж `ALL_QUEUES` (рядом с `USER_EMAIL_QUEUE,`). Routing-правила НЕ менять (ключ `RoutingKey.USER_EMAIL` уже покрыт правилом `("admin", "user.email.*")`).

- [ ] **Step 5: Green**

Run: `cd event-schemas && uv run pytest tests/test_user_payloads.py tests/test_queues.py -v`
Expected: PASS. Существующие топологические тесты (`test_queue_names_are_unique`, `test_canonical_queue_arguments`, `test_every_routing_rule_destination_has_a_bound_queue`, `test_no_two_services_consume_the_same_queue`) тоже зелёные (имя новой очереди уникально; её binding уже имеет правило).

- [ ] **Step 6: Commit**

```bash
cd event-schemas
uv run ruff check --fix . && uv run ruff format .
git add event_schemas/user.py event_schemas/queues.py tests/test_user_payloads.py tests/test_queues.py
git commit -m "feat: booking_uid in email-change payload + events.user.email.booking fan-out queue

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Релиз event-schemas 0.5.0 (bump + локальный тег)

**Files:**
- Modify: `pyproject.toml` (строка 3, `version`)
- Modify: `event_schemas/__init__.py` (строка ~80, `__version__`)

- [ ] **Step 1: Bump версий**

В `pyproject.toml`: `version = "0.5.0"`.
В `event_schemas/__init__.py`: `__version__ = "0.5.0"`.
(`mapping.py`/`types.py` НЕ трогать — новый тип события не добавляется; `__init__.py` ре-экспорт новой очереди НЕ требуется — по текущему паттерну именованные queue-константы, кроме `USER_SYNCED_QUEUE`, не ре-экспортируются.)

- [ ] **Step 2: Полный прогон + lint**

Run: `cd event-schemas && uv run ruff check --fix . && uv run ruff format . && uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 3: Commit + локальный тег** (push откладывается до Task 11)

```bash
cd event-schemas
git add pyproject.toml event_schemas/__init__.py
git commit -m "chore: release event-schemas 0.5.0 (email-change booking_uid + fanout queue)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git tag v0.5.0
```

> **Controller note:** тег `v0.5.0` локальный; пушится в Task 11 (user-gated). До этого event-booking использует editable path source.

---

# PHASE 2 — event-db-sync (репо `event-db-sync/`, ветка `main`)

### Task 3: Триггерная функция уважает `app.sync_suppress`

**Files:**
- Modify: `event_db_sync/adapters/calcom_triggers.py` (`TRIGGER_DDL`)
- Modify (append): `tests/test_calcom_triggers.py`

- [ ] **Step 1: Падающий тест** — добавить в `tests/test_calcom_triggers.py`:

```python
def test_ddl_respects_suppress_guc() -> None:
    from event_db_sync.adapters.calcom_triggers import TRIGGER_DDL

    assert "current_setting('app.sync_suppress', true)" in TRIGGER_DDL
    # подавление стоит ДО pg_notify
    suppress_idx = TRIGGER_DDL.index("app.sync_suppress")
    notify_idx = TRIGGER_DDL.index("pg_notify")
    assert suppress_idx < notify_idx
```

- [ ] **Step 2: Red**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_triggers.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализация** — в `event_db_sync/adapters/calcom_triggers.py` заменить тело функции в `TRIGGER_DDL`, добавив guard в начало `BEGIN`:

```python
TRIGGER_DDL = """
CREATE OR REPLACE FUNCTION user_sync_notify() RETURNS trigger AS $$
BEGIN
  IF current_setting('app.sync_suppress', true) = 'on' THEN
    RETURN NEW;
  END IF;
  PERFORM pg_notify(
    'user_sync',
    json_build_object('table', TG_TABLE_NAME, 'id', NEW.id)::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_sync_attendee ON "Attendee";
CREATE TRIGGER user_sync_attendee
  AFTER INSERT OR UPDATE ON "Attendee"
  FOR EACH ROW EXECUTE FUNCTION user_sync_notify();

DROP TRIGGER IF EXISTS user_sync_users ON "users";
CREATE TRIGGER user_sync_users
  AFTER INSERT OR UPDATE ON "users"
  FOR EACH ROW EXECUTE FUNCTION user_sync_notify();
"""
```
(`current_setting('app.sync_suppress', true)` со вторым аргументом `true` = «missing_ok», возвращает NULL если GUC не выставлен — тогда условие ложно и NOTIFY идёт как обычно. Кастомный GUC с точкой ставится без superuser.)

- [ ] **Step 4: Green**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_triggers.py -v && uv run pytest -q`
Expected: PASS (все остальные тесты не затронуты; существующие assert'ы про `CREATE OR REPLACE FUNCTION` / `pg_notify('user_sync'` / `DROP TRIGGER IF EXISTS` остаются истинными).

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
uv run ruff check --fix . && uv run ruff format .
git add event_db_sync/adapters/calcom_triggers.py tests/test_calcom_triggers.py
git commit -m "feat: trigger honors app.sync_suppress GUC to avoid re-sync loop on admin Attendee writes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> Идемпотентность: `CREATE OR REPLACE FUNCTION` переприменяется при старте event-db-sync — новая версия функции встанет автоматически на уже существующих БД.

---

# PHASE 3 — event-booking (репо `event-booking/`, ветка `feat/attendee-email-sync`)

### Task 4: Перейти на локальную event-schemas (dev) + создать ветку

**Files:**
- Modify: `event-booking/pyproject.toml` (строка 62, `[tool.uv.sources]`)

- [ ] **Step 1: Ветка**

```bash
cd event-booking
git checkout -b feat/attendee-email-sync
```

- [ ] **Step 2: Переключить source на editable path**

В `event-booking/pyproject.toml` заменить:
```toml
event-schemas = { git = "https://github.com/Lelikov/event-schemas.git", tag = "v0.3.0" }
```
на:
```toml
event-schemas = { path = "../event-schemas", editable = true }
```

- [ ] **Step 3: Re-lock + проверка версии**

Run: `cd event-booking && uv lock && uv sync && uv run python -c "import event_schemas; print(event_schemas.__version__)"`
Expected: `0.5.0`. Также проверить импорт новой очереди:
`uv run python -c "from event_schemas.queues import USER_EMAIL_BOOKING_QUEUE; print(USER_EMAIL_BOOKING_QUEUE.name)"` → `events.user.email.booking`.

- [ ] **Step 4: Прогон тестов (должны остаться зелёными)**

Run: `cd event-booking && uv run pytest -q`
Expected: PASS (схема обратно совместима; новых обращений ещё нет).

- [ ] **Step 5: Commit**

```bash
cd event-booking
git add pyproject.toml uv.lock
git commit -m "chore: dev-pin event-schemas to local path (for 0.5.0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: cal.com адаптер — `update_attendee_email`

**Files:**
- Modify: `event_booking/adapters/db.py` (`BookingDatabaseAdapter`)
- Modify: `event_booking/interfaces/db.py` (`IBookingDatabaseAdapter`)
- Modify: `tests/adapters/test_db.py` (расширить `FakeExecutor`, добавить тест)

- [ ] **Step 1: Падающий тест** — в `tests/adapters/test_db.py` добавить в класс `FakeExecutor` метод (если его ещё нет):

```python
    async def execute_in_transaction(self, statements: list[tuple[str, dict]]) -> None:
        self.calls.append(("__tx__", statements))
```

и тест:

```python
class TestUpdateAttendeeEmail:
    async def test_suppresses_sync_and_updates_attendee_by_booking_uid(self) -> None:
        executor = FakeExecutor()
        adapter = BookingDatabaseAdapter(executor)
        await adapter.update_attendee_email(
            booking_uid="book-123", old_email="old@example.com", new_email="new@example.com"
        )
        marker, statements = executor.calls[0]
        assert marker == "__tx__"
        suppress_sql, suppress_params = statements[0]
        update_sql, update_params = statements[1]
        assert "SET LOCAL app.sync_suppress = 'on'" in suppress_sql
        assert 'UPDATE "Attendee"' in update_sql
        assert 'FROM "Booking" WHERE uid = :booking_uid' in update_sql
        assert "lower(email) = lower(:old_email)" in update_sql
        assert update_params == {
            "booking_uid": "book-123",
            "old_email": "old@example.com",
            "new_email": "new@example.com",
        }
```

- [ ] **Step 2: Red**

Run: `cd event-booking && uv run pytest tests/adapters/test_db.py -k UpdateAttendeeEmail -v`
Expected: FAIL (`update_attendee_email` нет).

- [ ] **Step 3: Реализация** — в `event_booking/adapters/db.py` добавить SQL-константы (рядом с другими `_*_SQL`) и метод (рядом с `reject_booking`):

```python
_SUPPRESS_SYNC_SQL = "SET LOCAL app.sync_suppress = 'on'"

_UPDATE_ATTENDEE_EMAIL_SQL = """
UPDATE "Attendee"
SET email = :new_email
WHERE "bookingId" = (SELECT id FROM "Booking" WHERE uid = :booking_uid)
  AND lower(email) = lower(:old_email)
"""
```

```python
    async def update_attendee_email(self, booking_uid: str, old_email: str, new_email: str) -> None:
        await self._executor.execute_in_transaction(
            [
                (_SUPPRESS_SYNC_SQL, {}),
                (
                    _UPDATE_ATTENDEE_EMAIL_SQL,
                    {"booking_uid": booking_uid, "old_email": old_email, "new_email": new_email},
                ),
            ]
        )
```

- [ ] **Step 4: Интерфейс** — в `event_booking/interfaces/db.py`, в Protocol `IBookingDatabaseAdapter` добавить:

```python
    async def update_attendee_email(self, booking_uid: str, old_email: str, new_email: str) -> None: ...
```

- [ ] **Step 5: Green** (и регрессионный no-DELETE инвариант)

Run: `cd event-booking && uv run pytest tests/adapters/test_db.py -v`
Expected: PASS. `TestCalcomRowsAreNeverDeleted` (грепает `*_SQL` константы на `DELETE`) остаётся зелёным — новые константы DELETE не содержат.

- [ ] **Step 6: Commit**

```bash
cd event-booking
uv run ruff check --fix . && uv run ruff format .
git add event_booking/adapters/db.py event_booking/interfaces/db.py tests/adapters/test_db.py
git commit -m "feat: update_attendee_email (SET LOCAL app.sync_suppress + UPDATE Attendee by booking_uid)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Консьюмер — подписка `register_user_email` + `handle_user_email_change`

**Files:**
- Modify: `event_booking/consumer.py` (`BookingConsumer`)
- Modify: `tests/test_consumer.py`

- [ ] **Step 1: Падающий тест** — в `tests/test_consumer.py` добавить (использует существующий `FakeContainer` из conftest, который на любой `get()` возвращает переданный объект):

```python
class TestUserEmailChange:
    async def test_updates_attendee_when_booking_uid_present(self) -> None:
        from unittest.mock import AsyncMock
        from event_booking.consumer import BookingConsumer
        from tests.conftest import FakeContainer

        adapter = AsyncMock()
        consumer = BookingConsumer(FakeContainer(adapter))
        await consumer.handle_user_email_change(
            {"old_email": "old@x.io", "new_email": "new@x.io", "booking_uid": "book-1"}
        )
        adapter.update_attendee_email.assert_awaited_once_with(
            booking_uid="book-1", old_email="old@x.io", new_email="new@x.io"
        )

    async def test_noop_without_booking_uid(self) -> None:
        from unittest.mock import AsyncMock
        from event_booking.consumer import BookingConsumer
        from tests.conftest import FakeContainer

        adapter = AsyncMock()
        consumer = BookingConsumer(FakeContainer(adapter))
        await consumer.handle_user_email_change(
            {"old_email": "old@x.io", "new_email": "new@x.io", "booking_uid": None}
        )
        adapter.update_attendee_email.assert_not_awaited()

    def test_register_user_email_subscribes_to_booking_queue(self) -> None:
        from unittest.mock import MagicMock
        from event_booking.consumer import BookingConsumer
        from event_schemas.queues import USER_EMAIL_BOOKING_QUEUE
        from tests.conftest import FakeContainer

        broker = MagicMock()
        consumer = BookingConsumer(FakeContainer(MagicMock()))
        consumer.register_user_email(broker, MagicMock(), USER_EMAIL_BOOKING_QUEUE)
        broker.subscriber.assert_called_once()
```

- [ ] **Step 2: Red**

Run: `cd event-booking && uv run pytest tests/test_consumer.py -k UserEmailChange -v`
Expected: FAIL.

- [ ] **Step 3: Реализация** — в `event_booking/consumer.py`:

Добавить импорт адаптера (вверху файла):
```python
from event_booking.adapters.db import BookingDatabaseAdapter
```
(Если возникнет циклический импорт — импортировать `BookingDatabaseAdapter` лениво ВНУТРИ `handle_user_email_change` и сообщить об этом.)

Добавить в класс `BookingConsumer` бизнес-метод (guard-clause, без else):
```python
    async def handle_user_email_change(self, data: dict) -> None:
        booking_uid = data.get("booking_uid")
        if not booking_uid:
            logger.info("user.email.change_requested without booking_uid; skipping cal.com Attendee update")
            return
        async with self._container() as request_container:
            db = await request_container.get(BookingDatabaseAdapter)
            await db.update_attendee_email(
                booking_uid=booking_uid,
                old_email=data["old_email"],
                new_email=data["new_email"],
            )
```

И метод регистрации второго подписчика (по образцу существующего `register`, но со своим фильтром и вызовом `handle_user_email_change`):
```python
    def register_user_email(self, broker: RabbitBroker, exchange: RabbitExchange, queue_spec: QueueSpec) -> None:
        queue = RabbitQueue(
            name=queue_spec.name,
            durable=True,
            routing_key=str(queue_spec.binding),
            arguments=queue_spec.arguments,
        )

        @broker.subscriber(queue, exchange)
        async def handle_user_email_message(msg: RabbitMessage) -> None:
            started_at = perf_counter()
            headers = {k: v for k, v in (msg.headers or {}).items() if isinstance(v, str)}
            body = msg.body if isinstance(msg.body, bytes) else json.dumps(msg.body).encode()
            try:
                http_msg = HTTPMessage(headers=headers, body=body)
                cloud_event = from_http(http_msg, JSONFormat())
            except Exception:
                metrics.record_message(queue=queue_spec.name, event_type="unknown", outcome="rejected", started_at=started_at)
                logger.exception("Failed to parse CloudEvent", headers=headers)
                return
            event_type = cloud_event.get_attributes().get("type", "")
            data = extract_event_data(cloud_event)
            if event_type != EventType.USER_EMAIL_CHANGE_REQUESTED.value:
                metrics.record_message(queue=queue_spec.name, event_type=event_type, outcome="ok", started_at=started_at)
                logger.warning("Unhandled event type on user-email queue, skipping", event_type=event_type)
                return
            try:
                await self.handle_user_email_change(data)
            except Exception:
                metrics.record_message(queue=queue_spec.name, event_type=event_type, outcome="rejected", started_at=started_at)
                raise
            metrics.record_message(queue=queue_spec.name, event_type=event_type, outcome="ok", started_at=started_at)
```
(Все используемые имена — `perf_counter`, `HTTPMessage`, `from_http`, `JSONFormat`, `metrics`, `extract_event_data`, `EventType`, `RabbitBroker`, `RabbitExchange`, `RabbitQueue`, `RabbitMessage`, `QueueSpec`, `logger`, `json` — уже импортированы в `consumer.py` для существующего `register`/`handle_message`. Если `EventType` не импортирован — добавить `from event_schemas.types import EventType`.)

- [ ] **Step 4: Green**

Run: `cd event-booking && uv run pytest tests/test_consumer.py -v`
Expected: PASS (существующие тесты dispatch/register не затронуты).

- [ ] **Step 5: Commit**

```bash
cd event-booking
uv run ruff check --fix . && uv run ruff format .
git add event_booking/consumer.py tests/test_consumer.py
git commit -m "feat: consume user.email.change_requested on fanout queue -> update cal.com Attendee

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Подключить новую подписку в `main.py` + обновить инвариант

**Files:**
- Modify: `event_booking/main.py`
- Modify: `event-booking/CLAUDE.md` (HARD INVARIANTS)

- [ ] **Step 1: Wiring в `main.py`** — добавить импорт очереди к существующему импорту из `event_schemas.queues`:
```python
from event_schemas.queues import BOOKING_LIFECYCLE_BOOKING_QUEUE, USER_EMAIL_BOOKING_QUEUE
```
и сразу после строки `await ensure_dead_letter_topology(broker, BOOKING_LIFECYCLE_BOOKING_QUEUE)` (≈стр. 45) добавить:
```python
    consumer.register_user_email(broker, exchange, USER_EMAIL_BOOKING_QUEUE)
    await ensure_dead_letter_topology(broker, USER_EMAIL_BOOKING_QUEUE)
```

- [ ] **Step 2: Проверка импорта приложения**

Run: `cd event-booking && uv run python -c "import event_booking.main"`
Expected: импортируется без ошибок.

- [ ] **Step 3: Обновить инвариант** — в `event-booking/CLAUDE.md` в разделе HARD INVARIANTS (где сказано «Allowed writes: Booking.status/rejectionReason updates and Booking.metadata merges») добавить пункт:
```
- `UPDATE "Attendee".email` — разрешено ТОЛЬКО для проброса смены email клиента из админки
  (событие user.email.change_requested с booking_uid). Выполняется с `SET LOCAL app.sync_suppress='on'`,
  чтобы не зациклить триггер event-db-sync. Никаких других записей в "Attendee".
```

- [ ] **Step 4: Полный прогон**

Run: `cd event-booking && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-booking
uv run ruff check --fix . && uv run ruff format .
git add event_booking/main.py CLAUDE.md
git commit -m "feat: subscribe event-booking to events.user.email.booking + document Attendee write invariant

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 4 — event-admin (репо `event-admin/`, ветка `feat/attendee-email-sync`)

### Task 8: Принять и опубликовать `booking_uid`

**Files:**
- Modify: `event_admin/routes.py` (`ChangeEmailRequest` ~строки 58-59; `change_user_email` publish ~509-518)
- Modify: `tests/test_change_email.py`

- [ ] **Step 1: Ветка**

```bash
cd event-admin
git checkout -b feat/attendee-email-sync
```

- [ ] **Step 2: Падающий тест** — добавить в `tests/test_change_email.py`:

```python
async def test_change_email_includes_booking_uid_in_payload(client, admin_headers, fakes, client_user) -> None:
    response = await client.post(
        f"/api/users/id/{client_user}/change-email",
        json={"new_email": "fresh@example.com", "booking_uid": "book-123"},
        headers=admin_headers,
    )
    assert response.status_code == 202
    event = fakes.publisher.published[0]
    assert event["data"]["booking_uid"] == "book-123"


async def test_change_email_booking_uid_optional(client, admin_headers, fakes, client_user) -> None:
    response = await client.post(
        f"/api/users/id/{client_user}/change-email",
        json={"new_email": "fresh@example.com"},
        headers=admin_headers,
    )
    assert response.status_code == 202
    event = fakes.publisher.published[0]
    assert event["data"]["booking_uid"] is None
```

- [ ] **Step 3: Red**

Run: `cd event-admin && uv run pytest tests/test_change_email.py -k booking_uid -v`
Expected: FAIL (поле отбрасывается / отсутствует в payload).

- [ ] **Step 4: Реализация** — в `event_admin/routes.py`:

Модель запроса:
```python
class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    booking_uid: str | None = None
```
В `change_user_email`, в `data` вызова `publisher.publish(...)` добавить ключ:
```python
        data={
            "user_id": str(user_id),
            "old_email": old_email,
            "new_email": new_email,
            "requested_by": user.sub,
            "booking_uid": body.booking_uid,
        },
```

- [ ] **Step 5: Green**

Run: `cd event-admin && uv run pytest tests/test_change_email.py -v`
Expected: PASS (существующие тесты публикации/валидации зелёные — `booking_uid` опционален).

- [ ] **Step 6: Commit**

```bash
cd event-admin
uv run ruff check --fix . && uv run ruff format .
git add event_admin/routes.py tests/test_change_email.py
git commit -m "feat: forward booking_uid in user.email.change_requested payload

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
(event-admin не зависит от event-schemas — бампать нечего.)

---

# PHASE 5 — event-admin-frontend (репо `event-admin-frontend/`, ветка `feat/attendee-email-sync`)

### Task 9: Прокинуть `bookingUid` в `requestEmailChange`

**Files:**
- Modify: `src/modules/participants/emailChangeApi.ts` (`requestEmailChange`, строки 23-28)
- Modify: `src/modules/participants/EmailChangeModal.tsx` (вызов на строке ~83)
- Create: `src/modules/participants/emailChangeApi.test.ts`

- [ ] **Step 1: Ветка**

```bash
cd event-admin-frontend
git checkout -b feat/attendee-email-sync
```

- [ ] **Step 2: Падающий тест** — `src/modules/participants/emailChangeApi.test.ts` (по образцу `src/modules/blacklist/blacklistApi.test.ts` — мок `fetch` через `vi.stubGlobal`, JWT через `setJwtToken`):

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { requestEmailChange } from './emailChangeApi.ts'
import { setJwtToken } from '../auth/storage.ts'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('requestEmailChange', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    sessionStorage.clear()
    setJwtToken('admin-token')
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('puts new_email and booking_uid in the body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { status: 'accepted' }))
    await requestEmailChange('user-1', 'new@example.com', 'book-123')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ new_email: 'new@example.com', booking_uid: 'book-123' })
  })

  it('sends undefined booking_uid when omitted', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { status: 'accepted' }))
    await requestEmailChange('user-1', 'new@example.com')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ new_email: 'new@example.com' })
  })
})
```
(Второй тест проверяет, что `JSON.stringify` опускает `booking_uid: undefined` — поле просто не появляется в теле. Если в проекте `VITE_API_BASE_URL` влияет на URL, тест проверяет только тело, не URL.)

- [ ] **Step 3: Red**

Run: `cd event-admin-frontend && npm test -- emailChangeApi`
Expected: FAIL.

- [ ] **Step 4: Реализация** — в `src/modules/participants/emailChangeApi.ts`:

```typescript
export async function requestEmailChange(
  userId: string,
  newEmail: string,
  bookingUid?: string,
): Promise<void> {
  await apiRequest(`/api/users/id/${userId}/change-email`, {
    method: 'POST',
    body: { new_email: newEmail, booking_uid: bookingUid },
  })
}
```
В `src/modules/participants/EmailChangeModal.tsx` (строка ~83) заменить вызов:
```typescript
    await requestEmailChange(userId, trimmed, bookingUid)
```
(`bookingUid` уже в `Props` модалки и в области видимости `handleSubmit`.)

- [ ] **Step 5: Green**

Run: `cd event-admin-frontend && npm test -- emailChangeApi`
Expected: PASS.

- [ ] **Step 6: Lint + Commit**

```bash
cd event-admin-frontend
npm run lint --if-present
git add src/modules/participants/emailChangeApi.ts src/modules/participants/EmailChangeModal.tsx src/modules/participants/emailChangeApi.test.ts
git commit -m "feat: send booking_uid from email-change modal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 6 — Релиз и документация

### Task 10: Документация контрактов и очередей

**Files:**
- Modify (root, ветка `main`): `docs/architecture/MESSAGE_CONTRACTS.md`
- Modify (event-receiver, ветка `feat/event-db-sync`): `event-receiver/QUEUES_DIGEST.md`
- Modify (event-booking, ветка `feat/attendee-email-sync`): `event-booking/QUEUES_DIGEST.md`

- [ ] **Step 1: MESSAGE_CONTRACTS** — в `docs/architecture/MESSAGE_CONTRACTS.md` обновить контракт `user.email.change_requested`: payload теперь несёт опциональный `booking_uid`; событие имеет ДВУХ консьюмеров (event-users `events.user.email` + event-booking `events.user.email.booking` через fan-out на routing key `events.user.email`); event-booking при наличии `booking_uid` обновляет `Attendee.email` в cal.com с подавлением триггера через `app.sync_suppress`.

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docs/architecture/MESSAGE_CONTRACTS.md
git commit -m "docs: user.email.change_requested carries booking_uid; fanout to event-booking

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: event-receiver QUEUES_DIGEST** — добавить очередь `events.user.email.booking` (consumer event-booking) к описанию fan-out по ключу `events.user.email`.

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver
git add QUEUES_DIGEST.md
git commit -m "docs: add events.user.email.booking fanout queue

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: event-booking QUEUES_DIGEST** — добавить, что event-booking теперь слушает `events.user.email.booking` и обрабатывает `user.email.change_requested` (обновление `Attendee.email`).

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-booking
git add QUEUES_DIGEST.md
git commit -m "docs: event-booking consumes events.user.email.booking for Attendee email update

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Пуш тега v0.5.0 + перепин event-booking (CONTROLLER-GATED)

> Выполнять ТОЛЬКО с явного разрешения пользователя — здесь происходит push в remote.

**Files:**
- `event-schemas` (push tag), `event-booking/pyproject.toml` (флип на tag).

- [ ] **Step 1: Push тега** (после одобрения):
```bash
cd event-schemas
git push origin feat/event-db-sync 2>/dev/null || true
git push origin v0.5.0
```

- [ ] **Step 2: Перепин event-booking на тег** — в `event-booking/pyproject.toml`:
```toml
event-schemas = { git = "https://github.com/Lelikov/event-schemas.git", tag = "v0.5.0" }
```

- [ ] **Step 3: Re-lock + тест**

Run: `cd event-booking && uv lock && uv sync && uv run python -c "import event_schemas; print(event_schemas.__version__)" && uv run pytest -q`
Expected: `0.5.0`, все тесты зелёные.

- [ ] **Step 4: Commit**

```bash
cd event-booking
git add pyproject.toml uv.lock
git commit -m "chore: pin event-schemas v0.5.0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Проверка прав на GUC + интеграционный smoke (CONTROLLER/опционально, требует Docker)

> Проверка открытого риска из спеки: ставится ли `SET LOCAL app.sync_suppress` cal-пользователем без superuser, и не зацикливается ли поток.

- [ ] **Step 1: Поднять стек**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d --build pg-calcom rabbitmq event-receiver event-admin event-booking event-db-sync event-users
```

- [ ] **Step 2: Проверка прав GUC на cal.com БД** — выполнить от cal-пользователя:
```bash
docker compose exec pg-calcom psql -U <calcom_user> -d <calcom_db> -c "BEGIN; SET LOCAL app.sync_suppress='on'; SELECT current_setting('app.sync_suppress', true); ROLLBACK;"
```
Expected: возвращает `on` без ошибки прав. Если ошибка `permission denied to set parameter` — эскалировать пользователю (fallback из спеки).

- [ ] **Step 3: E2E smoke** — создать бронирование через `scripts/calcom_sim.py create`, затем выполнить смену email через event-admin (`POST /api/users/id/{userId}/change-email` с `booking_uid`), и проверить:
  - `SELECT email FROM "Attendee" a JOIN "Booking" b ON a."bookingId"=b.id WHERE b.uid='<uid>';` → новый email.
  - В логах event-db-sync НЕТ нового `user.upserted` от этого UPDATE (триггер подавлен).

- [ ] **Step 4: Записать результат** в `event-booking/docs/AUDIT.md` (короткая заметка, что проброс проверен e2e и петля не возникает). Закоммитить в event-booking репо.

---

## Self-review

- **Spec coverage:** booking_uid в payload (T1) + проброс фронт→admin (T8/T9); fan-out очередь (T1) + подписка event-booking (T6/T7); UPDATE Attendee по Booking.uid→id→bookingId match old_email (T5); подавление триггера через GUC (T3 + T5 SET LOCAL); расширение инварианта (T7); event-receiver/event-users НЕ перепиниваются (подтверждено в исследовании, отражено в conventions); риск прав GUC (T12). ✅
- **Placeholder scan:** конкретный код во всех шагах; «open risk» вынесен в проверочную Task 12, не в реализацию. ✅
- **Type consistency:** `update_attendee_email(booking_uid, old_email, new_email)` одинаков в адаптере (T5), интерфейсе (T5), консьюмере (T6), тесте (T5/T6); `USER_EMAIL_BOOKING_QUEUE`/`app.sync_suppress`/`booking_uid` пишутся одинаково везде. ✅
- **No-elif:** guard-clause в `handle_user_email_change` (T6). ✅
- **Идемпотентность:** `lower(email)=lower(:old_email)` — повтор после успеха не матчит (0 строк). ✅
