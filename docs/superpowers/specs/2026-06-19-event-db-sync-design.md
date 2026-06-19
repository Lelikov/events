# event-db-sync — синхронизатор cal.com → event-users (дизайн)

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

Заменить HTTP-опрос CRM (старый эндпоинт `calendar-bot/app/routes.py::get_users`, который
дёргал `event-users/event_users/crm`) на событийную синхронизацию **внутри приватной сети**.
Обе БД (cal.com и event-users) теперь в одной приватной сети — HTTP-хоп между ними не нужен.

Новый сервис `event-db-sync` слушает изменения в БД cal.com через триггер + `pg_notify`,
формирует CloudEvent и публикует его **напрямую в RabbitMQ**. `event-users` апсёртит
пользователя, `event-saver` дозаполняет `user_id` у бронирований — событийно, near-real-time.

Имя `event-db-sync` универсальное: паттерн «триггер → NOTIFY → событие» обобщаемый, в будущем
можно подключить и другие таблицы/БД. Сейчас сконфигурирован под cal.com `Attendee` и `users`.

## Контекст (что уже есть)

- **event-users**: сегодня опрашивает CRM по HTTP (`CrmClient.fetch_users` → `GET /users`,
  AES-256-CBC, раз в 5 минут, по умолчанию выключено `is_sync_enabled=False`). Апсёрт —
  `upsert_user_from_crm(email, role, time_zone, name, contacts)` с `ON CONFLICT (email, role)`,
  обновляет `time_zone` через `COALESCE`. Консьюмер слушает очередь `events.user.email`
  (приоритетная, `x-max-priority:10`), но обрабатывает только `ce-type=user.email.change_requested`.
- **Уникальность пользователей**: составной ключ `(email, role)` (`uq_users_email_role`), а не
  email сам по себе. Один email может существовать и как `client`, и как `organizer`.
  Требование «email уникальны» выполняется **в пределах роли**.
- **event-saver**: таблицы `participants` больше нет — идентичность участника хранится в
  колонках `organizer_user_id` / `client_user_id` (оба UUID, nullable) на `bookings`. Уже есть
  `UserIdBackfillService` — **HTTP-поллер** (раз в 5 мин), который тянет email из сохранённого
  payload события (`events.payload->normalized->participants`), резолвит через event-users
  `GET /api/users/by-identity?email=&role=` и заполняет NULL-колонки.
- **Приоритеты** — AMQP-свойство `priority` на сообщении, берётся из `EVENT_PRIORITIES[event_type]`
  (`CRITICAL=10`). Упорядочивание работает **только внутри одной очереди**, не между сервисами.
- **Паттерн прямой публикации** — `event-receiver/.../publisher.py` строит бинарный CloudEvent
  через `to_binary(CloudEvent, JSONFormat())` и оборачивает payload в конверт
  `{original, normalized:{participants:[...]}}` (`event_schemas.envelope`).

## Поток данных (end-to-end)

```
cal.com DB  ──(триггер AFTER INSERT/UPDATE на Attendee, users)──► pg_notify('user_sync', {table,id})
                                                     │
                                          event-db-sync (LISTEN через asyncpg)
                                                     │  SELECT полной строки → UserUpsertedPayload
                                                     ▼  публикация НАПРЯМУЮ в exchange "events"
                            ce-type=user.upserted, source=crm-sync, priority=CRITICAL(10)
                                          routing key: events.user.email  (существующая очередь)
                                                     │
                                          event-users (существующий консьюмер)
                                          upsert_user_from_crm(email, role, time_zone, name, contacts)
                                          получает user_id (UUID PK)
                                                     │  публикация НАПРЯМУЮ
                            ce-type=user.synced, source=event-users, priority=CRITICAL(10)
                                          routing key: events.user.synced  (НОВАЯ очередь)
                                                     ▼
                                          event-saver (новый консьюмер)
                                          UPDATE bookings SET {organizer|client}_user_id=:user_id
                                            WHERE <совпадение email из payload> AND col IS NULL
```

**Соответствие ролей:** cal.com `Attendee` → `role=client`; cal.com `users` → `role=organizer`.

**Гонка с вебхуком бронирования** решена дозаполнением (backfill): путь вебхука и обогащение
в event-receiver **не меняются**. Если пользователь уже есть в event-users на момент ingress —
`user_id` проставляется сразу; если нет — `user.synced` дозаполняет его в течение миллисекунд.
Схема идентичности пользователей не меняется (детерминированные UUID — слишком инвазивно, вне scope).

## Компонент 1: сервис `event-db-sync`

Отдельный Python 3.14 сервис, по соглашениям монорепо (FastAPI для `/health` + `/metrics`,
Dishka, ruff line-120, без `elif`/`else`, frozen dataclass DTO, dual CI GH+GitLab, Helm-чарт,
запись в docker-compose). Свой git-репозиторий `event-db-sync`.

### Триггер (DDL, владелец — сам сервис, идемпотентно при старте)

`CREATE OR REPLACE FUNCTION user_sync_notify() RETURNS trigger` + триггеры
`AFTER INSERT OR UPDATE ON "Attendee"` и `AFTER INSERT OR UPDATE ON "users"`:

```sql
CREATE OR REPLACE FUNCTION user_sync_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'user_sync',
    json_build_object('table', TG_TABLE_NAME, 'id', NEW.id)::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

- Payload NOTIFY — только `{table, id}` (держим сильно ниже лимита 8 КБ); полную строку сервис
  до-SELECT-ит сам. Это снимает гонку «строка ещё не закоммичена» — NOTIFY доставляется после COMMIT.
- DDL применяется **идемпотентно при старте** сервиса (`CREATE OR REPLACE FUNCTION` +
  `DROP TRIGGER IF EXISTS ... ; CREATE TRIGGER ...`). Документируется как **санкционированное
  аддитивное исключение** из правила «cal.com никогда не мигрируем» — это интеграционный хук,
  а не миграция схемы cal.com.

### Слушатель (listener)

- `asyncpg` raw-соединение, `connection.add_listener('user_sync', callback)`.
- На уведомление: распарсить `{table, id}`, выполнить SELECT полной строки в зависимости от таблицы,
  смапить в `UserUpsertedPayload`, опубликовать.
- Маппинг полей:
  - `Attendee` → `role=client`, `email`, `time_zone = "timeZone"`, `name`.
  - `users`    → `role=organizer`, `email`, `time_zone = "timeZone"`, `name`.

### Публикатор (publisher)

- FastStream `RabbitBroker` (или aio-pika) → exchange `events`, routing key `events.user.email`,
  бинарный CloudEvent + конверт `{original, normalized:{participants:[]}}` через
  `event_schemas.envelope`. `priority=CRITICAL(10)`. `source=crm-sync`, `ce-type=user.upserted`.
- `ce-id` детерминированный: `uuid5(NAMESPACE, f"{table}:{id}:{updated_at}")` — для идемпотентности
  при повторной доставке и при пересечении NOTIFY и reconcile-сканирования.

### Watermark reconcile (надёжность)

LISTEN/NOTIFY **теряет** уведомления, пока сервис отключён. Поэтому:

- Собственная маленькая Postgres-БД сервиса, таблица:
  ```sql
  CREATE TABLE sync_state (
    source         text PRIMARY KEY,   -- 'attendee' | 'users'
    last_id        bigint,
    last_updated_at timestamptz
  );
  ```
- На старте и периодически (таймер) сканировать в cal.com строки новее watermark и до-эмитить
  пропущенные. Это же служит **разовым backfill при переключении** (cutover).
- **Проверка на этапе плана:** убедиться, что `Attendee` имеет `updatedAt`. Если нет — использовать
  `id` как high-water для INSERT-ов + join `Booking.updatedAt` для отлова изменений `timeZone`.
  Для `users` `updatedAt`/`createdAt` есть.

### Конфигурация (strict, env)

- `CALCOM_DATABASE_URL` — DSN cal.com (read + применение триггера).
- `DATABASE_URL` — DSN собственной БД (sync_state).
- `RABBITMQ_URL` — брокер.
- `RECONCILE_INTERVAL_SECONDS` (default 300).

## Компонент 2: изменения в `event-schemas` (нужен новый git-тег → re-lock зависимых)

- `RoutingKey.USER_SYNCED = "events.user.synced"`.
- Новый `QueueSpec` `events.user.synced` (`consumer="event-saver"`, `x-max-priority:10`, DLQ-компаньон).
- Правила маршрутизации (`ROUTING_RULES`):
  - `RoutingRuleSpec(RoutingKey.USER_EMAIL, "crm-sync", "user.upserted")` → переиспользуем
    существующую очередь `events.user.email` (консьюмер event-users).
  - `RoutingRuleSpec(RoutingKey.USER_SYNCED, "event-users", "user.synced")` → новая очередь.
- Payload-модели в `event_schemas/user.py`:
  - `UserUpsertedPayload {email: str, role: str, time_zone: str | None, name: str | None, contacts: list}`.
  - `UserSyncedPayload {email: str, role: str, user_id: UUID, time_zone: str | None}`.
- `EVENT_PRIORITIES`: `user.upserted = CRITICAL`, `user.synced = CRITICAL`.

## Компонент 3: изменения в `event-users`

- Консьюмер `events.user.email`: добавить ветку обработки `ce-type=user.upserted` (помимо
  существующего `user.email.change_requested`) → `upsert_user_from_crm(email, role, time_zone, name, contacts)`.
- После **успешного** апсёрта опубликовать `user.synced` (с уже сминченным `user_id`) **напрямую**
  в exchange `events`, routing key `events.user.synced`, `source=event-users`, `priority=CRITICAL`.
  Новый маленький публикатор (event-users умеет коннектиться к брокеру — добавляем publish).
- Старый HTTP-поллер CRM (`CrmSyncRunner`) отключить/вывести из эксплуатации (оставить код или удалить —
  решить в плане; по умолчанию `is_sync_enabled=False`, так что достаточно не включать и пометить deprecated).

## Компонент 4: изменения в `event-saver`

- Новый subscriber на очередь `events.user.synced` (добавляется в `SAVER_QUEUES` автоматически через
  фильтр `consumer == "event-saver"`).
- Обработчик `user.synced`: переиспользовать существующий backfill-`UPDATE`, но ключевать по `user_id`
  **из события** (HTTP-резолв не нужен — `user_id` уже в payload). Найти email участника из сохранённого
  payload (как в `_SELECT_LATEST_EMAIL`), сопоставить роль с колонкой
  (`organizer_user_id`/`client_user_id`), `UPDATE ... WHERE col IS NULL`.
- Существующий HTTP-поллер `UserIdBackfillRunner` оставить как медленную страховочную сеть.

## Надёжность, упорядочивание, идемпотентность

- **Упорядочивание/гонка:** `user.upserted` — CRITICAL внутри `events.user.email`; `user.synced`
  эмитится **только после** успешного апсёрта, поэтому `user_id` гарантированно существует, когда
  event-saver получает событие — внутренней гонки fan-out нет.
- **Идемпотентность:** `ce-id` детерминированный; апсёрт в event-users — `ON CONFLICT (email, role)`;
  `UPDATE ... WHERE col IS NULL` в event-saver идемпотентен по построению.
- **Сбои:** ошибка публикации/БД → уведомление восстановимо ближайшим reconcile-сканом; сбои консьюмеров
  уходят в DLX как обычно.

## Тестирование

- **event-db-sync:** триггер реально шлёт NOTIFY (на настоящем PG); listener→publish; reconcile
  доэмитит пропущенные строки по watermark; маппинг `Attendee→client` / `users→organizer`;
  обновление `time_zone` у существующего email (повтор Attendee по email).
- **event-users:** обработчик `user.upserted` апсёртит и публикует `user.synced` с `user_id`.
- **event-saver:** `user.synced` дозаполняет нужную колонку по email; идемпотентность; защита `WHERE col IS NULL`.

## За рамками (YAGNI)

- Детерминированные user_id / смена схемы идентичности.
- Изменения обогащения в event-receiver.
- Синхронизация удалений (DELETE) пользователей — пока только INSERT/UPDATE.
- Синхронизация других таблиц/БД (имя сервиса оставляет задел, но реализуем только cal.com users/attendee).
