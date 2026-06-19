# Проброс смены email клиента в cal.com Attendee (дизайн)

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

При смене email клиента в бронировании через админку (кнопка «Изменить email») новый email
должен записываться и в cal.com БД, в таблицу `"Attendee"` — в строке, относящейся к
редактируемому бронированию.

Связь: `booking_uid` → `Booking.uid` → `Booking.id` → `Attendee.bookingId`. В найденной строке
`Attendee` (где `email = old_email`) меняем `email` на `new_email`.

**Сейчас функционала НЕТ:** смена email обновляет только `users` в БД event-users; cal.com
`Attendee` никто не трогает (event-booking по инварианту пишет только в `Booking`).

## Решения (из брейнсторма)

1. **Область:** только текущее бронирование (нужен `booking_uid` в событии).
2. **Механизм доставки:** расширить существующее событие `user.email.change_requested`
   (добавить `booking_uid`) + **fan-out**: новая очередь для event-booking на тот же routing key
   `events.user.email`. Одно событие — два консьюмера (event-users + event-booking).
3. **Писатель в cal.com:** event-booking (владелец записи в cal.com, имеет SqlExecutor).
4. **Петля с триггером event-db-sync:** подавлять. Через **кастомный GUC** `app.sync_suppress`
   (НЕ `session_replication_role` — тот требует superuser, которого в managed PG не будет;
   кастомные GUC с точкой ставятся без superuser).

## Контекст (текущая реализация)

- Фронт: `EmailChangeModal` знает `bookingUid`, но `emailChangeApi.requestEmailChange` шлёт
  `POST /api/users/id/{userId}/change-email` с телом `{new_email}` — **без booking_uid**.
- event-admin `change_user_email` (`routes.py`): валидация (role=client, уникальность → 409),
  публикует `user.email.change_requested`, `source=admin`, `data={user_id, old_email, new_email,
  requested_by}` — **без booking_uid**.
- event-schemas: `UserEmailChangeRequestedPayload {user_id, old_email, new_email, requested_by}`.
  Маршрут: `(admin, user.email.*) → events.user.email` → очередь `USER_EMAIL_QUEUE`
  (consumer event-users).
- event-users `handle_email_change`: `UPDATE users SET email…, email_source='admin'`, контакты,
  webhook outbox. cal.com не трогает.
- event-booking: подключение к cal.com через `CALCOM_POSTGRES_DSN`, адаптер
  `BookingDatabaseAdapter` (`adapters/db.py`) поверх `ISqlExecutor` (`adapters/sql.py`, есть
  `execute`/`execute_in_transaction`). Слушает `BOOKING_LIFECYCLE_BOOKING_QUEUE`. Пишет ТОЛЬКО в
  `Booking` (status/rejectionReason/metadata) — `Attendee` только читает (`LEFT JOIN`).
  HARD-инвариант в `event-booking/CLAUDE.md` запрещает запись в `Attendee`.
- event-db-sync: триггер `user_sync_notify()` + `AFTER INSERT OR UPDATE ON "Attendee"`/`"users"`
  шлёт `pg_notify('user_sync', {table,id})` (`adapters/calcom_triggers.py`, `TRIGGER_DDL`).

## Поток данных (целевой)

```
Админка (EmailChangeModal)
  └─ POST /api/users/id/{userId}/change-email  {new_email, booking_uid}
        │
event-admin change_user_email (валидация без изменений)
  └─ publish user.email.change_requested, source=admin
       data = {user_id, old_email, new_email, requested_by, booking_uid}
       routing key: events.user.email
        ├─► events.user.email          ──► event-users (UPDATE users SET email…; booking_uid игнор)
        └─► events.user.email.booking  ──► event-booking (НОВАЯ очередь, fan-out на тот же ключ)
               если booking_uid задан — одна транзакция к cal.com:
                 SET LOCAL app.sync_suppress = 'on';
                 UPDATE "Attendee" SET email = :new_email
                   WHERE "bookingId" = (SELECT id FROM "Booking" WHERE uid = :booking_uid)
                     AND email = :old_email;
               триггер user_sync_notify видит app.sync_suppress='on' → НЕ шлёт pg_notify
```

## Компоненты по сервисам

### 1. event-admin-frontend
- `src/modules/participants/emailChangeApi.ts` — `requestEmailChange(userId, newEmail, bookingUid)`
  кладёт `booking_uid` в тело: `{ new_email, booking_uid }`.
- `EmailChangeModal.tsx` — передаёт `bookingUid` (уже доступен) в `requestEmailChange`.

### 2. event-admin
- `change_user_email` (`event_admin/routes.py`): принять опциональный `booking_uid` из тела
  запроса (модель запроса += `booking_uid: str | None`), добавить его в `data` публикуемого
  события. Логика валидации (role=client, уникальность, 409) не меняется.

### 3. event-schemas (→ v0.5.0)
- `UserEmailChangeRequestedPayload += booking_uid: str | None = Field(None, ...)` — обратно
  совместимо (старые продюсеры/события без поля валидны).
- Новая очередь `USER_EMAIL_BOOKING_QUEUE = QueueSpec(name="events.user.email.booking",
  binding=RoutingKey.USER_EMAIL, consumer="event-booking")`; добавить в `ALL_QUEUES`. Это
  fan-out: две очереди (`events.user.email` event-users + `events.user.email.booking`
  event-booking) забинднены на один routing key `events.user.email`, обе получают копию.
- Routing-правила НЕ меняются (тот же ключ).
- Bump версии до `0.5.0`, синхронизировать `__init__.__version__`, тег `v0.5.0`, перепин
  зависимых, использующих новую схему (event-booking — новый консьюмер; event-users — payload
  расширен; event-admin — если импортирует схему).

### 4. event-db-sync
- Обновить `TRIGGER_DDL` (`adapters/calcom_triggers.py`): в `user_sync_notify()` добавить в
  начало:
  ```sql
  IF current_setting('app.sync_suppress', true) = 'on' THEN
    RETURN NEW;
  END IF;
  ```
  перед `PERFORM pg_notify(...)`. Применяется идемпотентно при старте
  (`CREATE OR REPLACE FUNCTION`). Тест: при `app.sync_suppress='on'` нотификации нет, иначе есть.

### 5. event-booking
- Расширить HARD-инвариант в `event-booking/CLAUDE.md`: разрешён `UPDATE "Attendee".email` (для
  проброса смены email из админки).
- cal.com адаптер (`adapters/db.py`): новый метод
  `update_attendee_email(booking_uid: str, old_email: str, new_email: str) -> int` — в одной
  транзакции `SET LOCAL app.sync_suppress = 'on'` затем UPDATE-SQL выше; возвращает число
  затронутых строк.
- Консьюмер: подписать новую очередь `USER_EMAIL_BOOKING_QUEUE`. Обработчик `ce-type ==
  "user.email.change_requested"`: распаковать `booking_uid`, `old_email`, `new_email` из
  `original`. Если `booking_uid` отсутствует/пуст → no-op (ack). Иначе вызвать
  `update_attendee_email(...)`.
- Перепин event-schemas на `v0.5.0`.

### 6. Документация
- `docs/architecture/MESSAGE_CONTRACTS.md`: `user.email.change_requested` теперь несёт
  опциональный `booking_uid` и имеет двух консьюмеров (event-users + event-booking via fan-out).
- `event-receiver/QUEUES_DIGEST.md`, `event-booking/QUEUES_DIGEST.md`: новая очередь
  `events.user.email.booking`.
- `event-booking/CLAUDE.md`: обновлённый инвариант (разрешена запись `Attendee.email`).

## Корректность, ошибки, идемпотентность

- **Идемпотентность:** `UPDATE ... AND email = :old_email`. При повторной доставке old_email уже
  заменён на new_email → 0 строк → no-op. Безопасно при переотправке/DLX-redelivery.
- **Порядок:** event-users (UPDATE users) и event-booking (UPDATE Attendee) получают независимые
  копии события; порядок между ними не важен (разные БД, разные данные).
- **Петля:** `app.sync_suppress='on'` (SET LOCAL — действует до конца транзакции) → триггер
  `user_sync_notify` выходит до `pg_notify` → event-db-sync не порождает обратный `user.upserted`.
- **Несколько Attendee в брони:** UPDATE затронет все строки этого бронирования с `email =
  old_email` (обычно одна — клиент). Это корректно для области «только это бронирование».
- **Сбой UPDATE / БД недоступна:** исключение → сообщение уходит в DLX (стандартное поведение
  консьюмера event-booking).
- **booking_uid не найден / Attendee нет:** UPDATE затронет 0 строк → no-op, ack (не ошибка).

## Тестирование

- **event-schemas:** `UserEmailChangeRequestedPayload` валиден с `booking_uid` и без него;
  `USER_EMAIL_BOOKING_QUEUE` в `ALL_QUEUES`, binding `events.user.email`, consumer `event-booking`;
  не попадает в `SAVER_QUEUES`; обе очереди на одном routing key (fan-out).
- **event-db-sync:** триггерная функция: при `app.sync_suppress='on'` — нет `pg_notify`; иначе —
  есть. (Юнит на текст DDL + при возможности интеграционный на живом PG.)
- **event-booking:** обработчик с `booking_uid` → корректный UPDATE-SQL (по `Booking.uid` →
  `Booking.id` → `Attendee.bookingId`, match `old_email`, с `SET LOCAL app.sync_suppress`);
  без `booking_uid` → no-op; идемпотентность (повтор → 0 строк).
- **event-admin:** `change_user_email` прокидывает `booking_uid` в payload; работает и без него.

## Открытый риск (проверить на этапе плана)

- Права cal-пользователя на `SET LOCAL app.sync_suppress`. Кастомные GUC с точкой в имени
  ставятся без superuser, но проверить на фикстуре `pg-calcom` (и при возможности на боевой
  cal.com БД). Если запрещено — fallback: разовая ручная верификация прав, либо иной способ
  пометки «изменение от нас» (например, временная таблица-маркер), решаемый в плане.

## За рамками (YAGNI)

- Смена email во всех бронированиях клиента (выбрана область «только это бронирование»).
- Изменение имени/часового пояса Attendee (только email).
- Двунаправленная синхронизация конфликтов (cal.com — внешняя система; пишем по требованию).
