# event-scheduling — интеграция бронирования с контуром events (срез 4a)

> Дизайн-спек. Дата: 2026-07-13.
> Первая половина среза 4: `event-scheduling` публикует `booking.lifecycle` CloudEvents в существующий контур `events` через транзакционный outbox, чтобы `event-saver` проецировал брони новой системы. Аддитивно — рядом с cal.com, не вместо.

## 0. Контекст и рамка

**Большая цель** (без изменений): своя система бронирования внутри `events`; cal.com постепенно уходит. **cal.com-вебхуки остаются** (решение пользователя) — интеграция аддитивная.

**Срезы:** 1 модель расписаний ✅ · 2 движок слотов ✅ · 3 write-side бронирования ✅ · **4a — публикация booking-событий → проекции (event-saver)** ← *этот спек* · 4a.2 — перевод `event-booking` на payload/API (чат/Jitsi/напоминания для новых броней) · 4b — Booker UI · 5 calendar-sync · 6 ЛК-редактор.

**Границы среза 4a — только проекции.** `event-scheduling` при create/reschedule/cancel публикует `booking.lifecycle` CloudEvent через `POST /event/booking` (event-receiver) → `event-saver` проецирует бронь в свою БД (проекции/админка). **Не входит:** чат/Jitsi/напоминания (`event-booking` — срез 4a.2), Booker UI (4b). cal.com-путь, `event-receiver`, `event-booking` — **не трогаем**.

### Критические факты контракта (из разведки монорепо)

- **`event-saver` — payload-driven.** Проецирует из `{original, normalized}`: `start_time`/`end_time` из `original`, `status` из типа события (`created`/`cancelled`; reschedule не меняет статус), участники из `normalized.participants[].user_id` (UUID), keyed by `role∈{organizer,client}`. Публикации CloudEvent **достаточно**.
- **`event-booking` — НЕ payload-driven** (для справки, не в скоупе): читает бронь из БД cal.com по `booking_uid` и no-op'ит, если строки нет. Наши брони не в cal.com → `event-booking` ничего не делает. Это ожидаемо; закрывается в 4a.2.
- **Контур email-first.** `event-receiver` резолвит `participants.user_id` из **email+role** на ingress (`_enrich_participants` → event-users `/api/users/by-identity`); payload-модели требуют `email` (`BookingParticipant.email` обязателен). У нас — UUID, email нет → **резолвим UUID→email** из `event-users` перед публикацией.
- **Публикация — через `POST /event/booking`** (event-receiver), тот же путь, что `event-booking` использует для follow-up'ов. Нет generic-endpoint'а.
  - Auth: заголовок `Authorization: <BOOKING_API_KEY>` — **сырой** ключ, НЕ `Bearer`.
  - CloudEvent **binary mode**: `ce-*` заголовки + JSON-тело. Обязательные: `ce-specversion:1.0`, `ce-id`, `ce-source:booking`, `ce-type`, `ce-time`; тело обязано содержать `booking_uid` (→ `ce-bookingid`).
  - Ответы: `202` accepted; `400` bad/невалидный payload; `401` bad key; `503` broker unavailable.
- **Типы событий** (`event-schemas/event_schemas/types.py`, source=`booking`, priority CRITICAL=10): `booking.created`, `booking.rescheduled`, `booking.cancelled` (+ `reassigned`/`rejected`/`reminder_sent` — нами не производятся). `event-receiver` принимает `booking.created` в форме `users`-списка и трансформирует.
- **Идемпотентность контура:** event-receiver in-memory dedupe (SHA256 по type+uid+data, 600с); `ce-idempotencykey`; event-saver дедуп по `event_id` PK + idempotency-индекс. Стабильный `ce-id` при retry → безопасные повторы.
- **`booking_uid`:** контур ключует всё по `ce-bookingid` (строка). Наш `booking.id` (UUID-строка) годится напрямую — не коллизирует с cuid-пространством cal.com.

## 1. Модель и границы

**Новый изолированный модуль `event_scheduling/publishing/`:**

| Файл | Ответственность | IO |
|---|---|---|
| `outbox_writer.py` | пишет строку в `outbox` через `SqlExecutor` (вызов из `BookingService` в **той же транзакции**, что create/cancel/reschedule) | DB (общая сессия) |
| `payload.py` | **чистое** построение тела `booking.*` из outbox-строки + резолвленных участников | нет |
| `receiver_client.py` | HTTP к `event-receiver`: `POST /event/booking`, auth `BOOKING_API_KEY` (сырой), binary CloudEvent | HTTP |
| `users_client.py` | HTTP к `event-users`: `GET /api/users/by-ids` → `{uuid: {email, time_zone?}}` | HTTP |
| `dispatcher.py` | фоновый поллер: pending → резолв → build → POST → sent/retry/failed | DB + HTTP |
| `interfaces.py` | Protocol'ы `IOutboxWriter`, `IReceiverClient`, `IUsersClient` | — |

**Границы:**
- **Владеет таблицей `outbox`** (миграция `0003`).
- **Booking write-path без внешних вызовов** — только `INSERT` в `outbox` в той же транзакции. Бронь не падает при недоступности event-users/event-receiver; доставка — забота диспетчера.
- **Фоновый диспетчер** стартует/останавливается в FastAPI-lifespan (`main.py`) — первый background-цикл сервиса (аккуратный in-process poller; отдельный воркер не нужен на этом масштабе).
- **Аддитивно:** публикуем в существующий `POST /event/booking`; cal.com/event-receiver/event-booking не трогаем. Потребитель — `event-saver`. `event-booking` no-op'ит (нет cal.com-строки) — ожидаемо до 4a.2.
- Стиль: Python 3.14, Dishka, `SqlExecutor` raw `:param`, frozen DTO, Pydantic только в schemas, **no elif/avoid else**, Ruff 120.

## 2. Схема `outbox` (миграция `0003`)

```
id              uuid pk (server_default gen_random_uuid())
event_ce_id     uuid not null   -- стабильный ce-id → идемпотентные retry
event_type      text not null   -- 'booking.created'|'booking.rescheduled'|'booking.cancelled'
booking_uid     text not null   -- = booking.id (UUID-строка) → ce-bookingid
payload         jsonb not null  -- доменные поля (UUID-ы, времена, previous_*, cancellation) — БЕЗ email
status          text not null default 'pending'   -- 'pending'|'sent'|'failed'
attempts        int  not null default 0
next_attempt_at timestamptz not null default now()
last_error      text null
created_at      timestamptz not null default now()
sent_at         timestamptz null
```
- `CHECK status IN ('pending','sent','failed')` — `ck_outbox_status`.
- `CHECK event_type IN ('booking.created','booking.rescheduled','booking.cancelled')` — `ck_outbox_type`.
- Индекс `(status, next_attempt_at)` — `ix_outbox_dispatch` (выборка диспетчером).

**`payload` jsonb по типу** (пишется в транзакции мутации брони):
- `booking.created`: `{host_user_id, client_user_id, start_time, end_time, attendee_time_zone}`.
- `booking.rescheduled`: `{host_user_id, client_user_id, start_time, end_time, previous_start_time, attendee_time_zone}`.
- `booking.cancelled`: `{host_user_id, client_user_id, start_time, end_time, cancellation_reason?, attendee_time_zone}`.

`reassigned` не производим (переназначения нет; reschedule in-place сохраняет `booking_uid`).

## 3. Запись в outbox (интеграция в BookingService)

`BookingService` получает зависимость `IOutboxWriter`. В существующих методах, **в той же транзакции** после мутации + change-log:
- `create` → `outbox_writer.write("booking.created", booking, ce_id=uuid)`.
- `reschedule` → `outbox_writer.write("booking.rescheduled", booking, previous_start=old_start)`.
- `cancel` → `outbox_writer.write("booking.cancelled", booking, reason=…)`.

`write` делает один `INSERT` через тот же `SqlExecutor` (та же сессия/транзакция → атомарно с бронью). `event_ce_id` генерится тут (стабилен для всех retry).

## 4. Диспетчер (`dispatcher.py`)

Asyncio-задача, старт/стоп в lifespan. Цикл каждые `OUTBOX_DISPATCH_INTERVAL` (деф. 5с):
1. `SELECT ... WHERE status='pending' AND next_attempt_at <= now() ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED`. (`SKIP LOCKED` — безопасность при будущем многоинстансном режиме; сейчас один диспетчер, `ORDER BY created_at` — доставка по порядку.)
2. На строку:
   - **Резолв email**: `users_client.by_ids([host_user_id, client_user_id])` → `{uuid: {email, time_zone?}}`.
   - **Build**: `payload.build_cloudevent(row, participants)` → `(ce_headers, body)` (см. §5).
   - **POST** `/event/booking` через `receiver_client`.
3. Исход:
   - `202` → `status='sent'`, `sent_at=now`.
   - **Транзиентно** (`503` / таймаут / event-users недоступен / email не найден — юзер мог не засинкаться) → `attempts++`, `next_attempt_at = now + min(300s, base * 2^attempts)`, `last_error`; остаётся `pending`.
   - **Перманентно** (`400` невалидный payload / `401` ключ) → `status='failed'` + `last_error` (лог/алерт).

**Идемпотентность:** `ce-id = event_ce_id` стабилен; downstream дедупит. «POST прошёл, пометить не успели» → повторный POST дедупится в event-receiver/event-saver.

## 5. Маппинг payload по типам (`payload.py`, чистое)

Тело под принятые `event-receiver` формы; `booking_uid` в теле; роли host→`organizer`, client→`client`; `time_zone` client = `attendee_time_zone` из брони, host = из резолва.

- **`booking.created`**: `{users:[{email,role:"organizer",time_zone},{email,role:"client",time_zone}], start_time, end_time, volunteer_id:host_user_id, client_id:client_user_id, booking_uid}`.
- **`booking.rescheduled`**: `{users:[…organizer,…client], start_time, end_time, previous_start_time, booking_uid}`.
- **`booking.cancelled`**: `{users:[…organizer,…client], cancellation_reason?, booking_uid}`.

`ce`-заголовки: `ce-specversion:1.0`, `ce-id:<event_ce_id>`, `ce-source:booking`, `ce-type:<event_type>`, `ce-time:<iso now>`.

## 6. Config + DI

**Config (`config.py`):** `EVENT_RECEIVER_URL`, `BOOKING_API_KEY`, `EVENT_USERS_URL`, `OUTBOX_DISPATCH_INTERVAL` (5с), `OUTBOX_BATCH_SIZE` (50), `OUTBOX_MAX_BACKOFF_SECONDS` (300). Docker-compose: указать на in-contour `event-receiver`/`event-users`.

**DI (`ioc.py`):** REQUEST-scope `IOutboxWriter`→`OutboxWriter(sql)`; `BookingService` получает `IOutboxWriter`. APP-scope HTTP-клиенты (`IReceiverClient`, `IUsersClient`) + диспетчер (запускается lifespan'ом, использует свой sessionmaker для фоновых транзакций — не request-scoped).

## 7. Тестирование

- **outbox_writer** (integration): create/cancel/reschedule пишут корректную `outbox`-строку в **той же транзакции** (откат брони откатывает и outbox; на успехе — ровно одна строка нужного типа с payload).
- **payload.py** (unit, чистое): корректные тела `created/rescheduled/cancelled` + ce-заголовки из outbox-строки + резолвленных участников; роли/tz верны.
- **dispatcher** (unit, fake `receiver_client`+`users_client`): `pending`→резолв→POST→`sent`; `503`→retry (`attempts++`, `next_attempt_at` в будущем, `pending`); `400`→`failed`; стабильный `ce-id` при повторе; email-not-found → retry (не `failed`); backoff растёт и капается.
- **Интеграция**: стаб `event-receiver` (локальный FastAPI/httpx-mock) — диспетчер шлёт правильные `ce-*`+тело на `/event/booking`; повторный dispatch той же строки → тот же `ce-id` (идемпотентность).
- **lifespan**: диспетчер стартует/останавливается чисто (нет висящих задач).
- Полный `pytest` зелёный (срезы 1–3 не сломаны — booking write-path теперь пишет outbox-строку; существующие booking-тесты ожидают outbox-строку или игнорируют её).

## 8. Открытые вопросы к следующим срезам (не блокируют 4a)

1. **`event-booking` payload/API-driven** (срез 4a.2): чтобы чат/Jitsi/напоминания срабатывали для новых броней — читать бронь из payload или `event-scheduling`-API при отсутствии cal.com-строки. Это модификация `event-booking`, свой спек.
2. **UUID→email резолв в `event-users`**: `GET /api/users/by-ids` должен возвращать `email` (+`time_zone`). Проверить контракт при реализации; если by-ids не отдаёт email — добавить поле или отдельный endpoint (минимальная правка event-users, согласовать).
3. **`failed`-строки outbox**: сейчас лог/алерт; позже — админ-ручка ре-драйва / метрика.
4. **Ordering при многоинстансном диспетчере**: `SKIP LOCKED` заложен; строгий per-booking порядок при N воркерах — если понадобится, добавить per-`booking_uid` сериализацию.

## 9. Определение готовности среза 4a

- Миграция `0003` (`outbox`) применяется; downgrade корректен.
- `BookingService` пишет `outbox`-строку в той же транзакции на create/reschedule/cancel.
- `payload.py`/`dispatcher.py`/`receiver_client.py`/`users_client.py` реализованы; диспетчер стартует в lifespan.
- Тесты §7 зелёные (вкл. интеграционный стаб event-receiver + идемпотентность + backoff); полный `pytest` + Ruff clean.
- `event-scheduling/CLAUDE.md` + `docs/` обновлены (модуль publishing, outbox, booking-события); корневой `docs/architecture/` — новый producer `booking.lifecycle` в топологии (аддитивно к cal.com); docker-compose — env на event-receiver/event-users.
- Ручная проверка: бронь в `event-scheduling` → через несколько секунд проекция в `event-saver` (booking_uid = booking.id), при поднятом контуре.
