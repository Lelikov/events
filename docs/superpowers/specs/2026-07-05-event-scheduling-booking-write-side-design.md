# event-scheduling — write-side бронирования (срез 3)

> Дизайн-спек. Дата: 2026-07-05.
> Третий срез замены внешнего CRM (форк cal.com): доменное создание/изменение броней поверх модели среза 1 и движка слотов среза 2.

## 0. Контекст и рамка

**Большая цель** (без изменений): cal.com отключается; `events` сам владеет расписаниями, слотами и бронированиями.

**Срезы:** (1) доменная модель расписаний — **готово, main**; (2) движок слотов — **готово, main**; (3) **write-side бронирования** ← *этот спек*; (4) Booker UI + интеграция с контуром `events` (публикация `booking.lifecycle` CloudEvents → event-booking/event-saver: чат/Jitsi/напоминания); (5) calendar-sync (опц.); (6) ЛК-редактор.

**Граница среза 3 — только доменное бронирование** (решение диалога): таблица `booking`, эндпоинты create/cancel/reschedule/get/list/history, защита от двойного бронирования, round-robin-назначение хоста, **реальный `BusyTimesSource`** (движок слотов наконец учитывает занятость + буферы), enforcement `booking_limit`. **Чистый HTTP — без RabbitMQ/CloudEvents** (интеграция с контуром — срез 4).

**Опорное:** модель — `event-scheduling/docs/DATA_MODEL.md` (8 таблиц); движок — модуль `event_scheduling/slots/` + seam `interfaces/busy_times.py` (`BusyTimesSource.get_busy(user_ids, window) -> list[BusyInterval]`, сейчас `StubBusyTimesSource → []`). Алгоритм cal.com — `~/PycharmProjects/calendar/docs/architecture/schedule-generation.md` §3.4 (getLuckyUser), §4.5 (лимиты).

**Ключевые решения (диалог):**
- **Anti-двойное-бронирование — Postgres exclusion constraint** (`btree_gist`) на сырых временах; буферы — в слое доступности, не в constraint.
- **Назначение хоста** — наименьшее число **предстоящих** confirmed-броней; тай-брейк «наименее недавно назначенному».
- **Клиент** — `client_user_id` (непрозрачный UUID → event-users) + `attendee_time_zone`; без дублирования PII.
- **Lifecycle** — soft-cancel (`status='cancelled'`); reschedule **in-place, тот же хост**; **append-only `booking_change_log`** (transition-log created/rescheduled/cancelled).
- **Оркестрация создания** — оптимистичная вставка + retry по exclusion-constraint над пулом свободных хостов (не пессимистичный лок, не reservations).
- **Лимиты** — per event_type; границы периода в **tz расписания хоста**.

## 1. Модуль и границы

**Новый изолированный модуль `event_scheduling/booking/`** (по образцу `slots/`):
- `dto.py`, `interfaces.py`, `read_adapter.py`, `write_adapter.py`, `assignment.py` (getLuckyUser — чистая функция), `limits.py` (границы периода + проверка, чистое), `busy_source.py` (`BookingBusyTimesSource`), `service.py` (оркестрация).
- `routers/booking.py`, `schemas/booking.py`.
- **Владеет таблицами** `booking`, `booking_change_log` (миграция `0002`, добавляет `btree_gist`).
- **Замыкает seam слотов:** `BookingBusyTimesSource` заменяет `StubBusyTimesSource` в DI; **DI-scope переезжает APP→REQUEST** (нужна `AsyncSession`) — как пометил финальный review среза 2.
- **Переиспользует:** движок слотов (`slots/domain.py`, `slots/timezones.py`) для пере-валидации доступности; `validation.py`; `errors.py`. Инжектируемый `Clock` (из `slots/`).
- Сервис **чистый HTTP** — CloudEvents в контур `events` не входят (срез 4).
- Стиль: Python 3.14, Dishka, `SqlExecutor` raw `:param`, frozen-dataclass DTO, Pydantic только в `schemas/`, **no `elif`/avoid `else`**, Ruff 120.

## 2. Схема (миграция `0002`)

Миграция сначала `op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")`.

### `booking`
```
id                 uuid pk (server_default gen_random_uuid())
event_type_id      uuid not null  FK -> event_type(id) ON DELETE RESTRICT
host_user_id       uuid not null      -- назначенный round-robin хост (ref event-users)
client_user_id     uuid not null      -- ref event-users
start_time         timestamptz not null
end_time           timestamptz not null
status             text not null default 'confirmed'   -- 'confirmed' | 'cancelled'
attendee_time_zone text not null          -- IANA, зафиксирована при брони
created_at         timestamptz not null default now()
updated_at         timestamptz not null default now()
```
Ограничения:
- `CHECK (end_time > start_time)` — `ck_booking_range`.
- `CHECK (status IN ('confirmed','cancelled'))` — `ck_booking_status`.
- **Exclusion (ядро):** `EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) WHERE (status = 'confirmed')` — `ex_booking_no_overlap`. Нет пересечения confirmed-броней одного хоста; cancelled не блокируют.

Индексы: `(host_user_id, status, start_time)` — счёт предстоящих + busy; `(event_type_id, status, start_time)` — лимиты; `(client_user_id)`.

### `booking_change_log` (append-only transition-log, без FK-cascade)
```
id             uuid pk
booking_id     uuid not null          -- без FK: история переживает всё
kind           text not null          -- 'created' | 'rescheduled' | 'cancelled'
from_start     timestamptz null
from_end       timestamptz null
to_start       timestamptz null
to_end         timestamptz null
actor_source   text not null
actor_user_id  uuid null
at             timestamptz not null default now()
```
`CHECK (kind IN ('created','rescheduled','cancelled'))`. Пишется в той же транзакции, что и мутация брони.

## 3. Flow создания (`POST /api/v1/bookings`, оптимистичный + retry)

Вход: `{event_type_id, client_user_id, start_time (UTC), attendee_time_zone}`. Длительность — из `event_type`. Одна транзакция запроса:

1. Загрузить `event_type` (config + хосты + буферы + лимиты) → `404` если нет. `end_time = start_time + duration_minutes`.
2. Валидация: IANA `attendee_time_zone` (иначе `422`); `start_time` в будущем (иначе `422`).
3. **Пере-валидация доступности на текущий момент** (движок слотов): для каждого хоста события — свободные UTC-интервалы (`host_availability_intervals` − занятость из `BookingBusyTimesSource` с буферами); хост «подходит», если `[start,end)` целиком влезает в его свободный интервал **и** `start ≥ now + min_booking_notice` (`now` — инжектируемый `Clock`). Множество свободных подходящих хостов пусто → `409`.
4. **getLuckyUser** (`assignment.py`): среди свободных — с наименьшим числом предстоящих confirmed-броней; тай-брейк «наименее недавно назначенному» (`MAX(created_at)` по хосту, NULL/never первым). Даёт упорядоченный список кандидатов (лучший первым) для retry.
5. **Enforcement `booking_limit`** (per event_type, период в tz **назначенного** хоста; см. §5) — превышение → `409`. Лимит event-type-глобален (счёт не зависит от хоста), поэтому проверяется один раз для лучшего кандидата; на retry (см. п.6) хост меняется, но счёт лимита тот же.
6. **Оптимистичный `INSERT`** с лучшим хостом. `IntegrityError` от exclusion-constraint (гонка) → исключить этого хоста, взять следующего кандидата из п.4, retry. Кандидаты кончились → `409`.
7. **`booking_change_log`** `created` (`to_*` = времена брони, `from_*` NULL), та же транзакция. → `201` `{booking}`.

## 4. Cancel / reschedule

**Cancel** — `POST /api/v1/bookings/{id}/cancel`:
- Загрузить (`404`). Уже `cancelled` → идемпотентно `200`, без второй лог-строки. Иначе `status='cancelled'`, `updated_at=now()`; лог `cancelled` (`from_*` = текущие времена, `to_*` NULL). Слот освобождается автоматически (constraint `WHERE confirmed`).

**Reschedule** — `POST /api/v1/bookings/{id}/reschedule`, body `{start_time}`:
- Загрузить (`404`). `cancelled` → `409`. `new_end = new_start + duration` (event_type брони).
- Валидация: `new_start` в будущем + `≥ now + min_booking_notice` (`422`/`409`).
- **Пере-валидация: тот же `host_user_id`** свободен в новое время (рабочие часы + занятость **без учёта самой этой брони** через `exclude_booking_id`, буферы). Не свободен → `409`.
- `UPDATE start_time/end_time/updated_at` in-place (exclusion пере-проверяется на UPDATE; своя строка сама с собой не конфликтует). Лог `rescheduled` (`from_*` старые, `to_*` новые). Хост **не** переназначается. → `200` `{booking}`.

## 5. `BookingBusyTimesSource` + лимиты

**`BookingBusyTimesSource`** реализует `BusyTimesSource` (Protocol среза 2):
- `get_busy(user_ids, window, exclude_booking_id=None) -> list[BusyInterval]`. Доп. опциональный `exclude_booking_id` не ломает Protocol (публичный `SlotService` зовёт `get_busy(users, window)`; booking-сервис при reschedule — с `exclude_booking_id`).
- SQL: confirmed-брони, где `host_user_id = ANY(:user_ids)`, `status='confirmed'`, `tstzrange(start,end) && :window`, `id <> :exclude` (если задан); JOIN к `event_type` за буферами.
- Каждая бронь → `[start − buffer_before, end + buffer_after]` (буферы её event_type).
- DI: `StubBusyTimesSource` → `BookingBusyTimesSource` (REQUEST-scope).

**Лимиты** (`limits.py`, per event_type, границы периода в tz расписания хоста):
- Для `start_time` вычисляем период (day/week/month/year) как **локальный календарный** интервал в tz хоста → UTC-границы (week — ISO, с понедельника; month/year — календарные в tz хоста). Чистая функция, табличные тесты.
- Считаем confirmed-брони `event_type_id` со `start_time` в UTC-границах:
  - `booking_count`: отклонить, если `count ≥ value`;
  - `booking_duration`: отклонить, если `сумма_минут + duration > value`.
- Несколько лимитов — все должны пройти; превышение → `409`.

## 6. API-контракты

Под `require_api_key`. Actor — заголовки `actor-source` / `actor-user-id` → change-log.

| Метод | Путь | Поведение |
|---|---|---|
| `POST` | `/api/v1/bookings` | Создать. Body `{event_type_id, client_user_id, start_time, attendee_time_zone}`. `201`/`409`/`404`/`422` |
| `GET` | `/api/v1/bookings/{id}` | Одна бронь `200`/`404` |
| `GET` | `/api/v1/bookings?host_user_id=…` \| `?client_user_id=…` (+`from`,`to`) | Список по хосту или клиенту, опц. диапазон; ровно один из фильтров обязателен (иначе `422`) |
| `POST` | `/api/v1/bookings/{id}/cancel` | Soft-cancel `200`; идемпотентно если уже cancelled |
| `POST` | `/api/v1/bookings/{id}/reschedule` | Body `{start_time}`. In-place, тот же хост. `200`/`409`/`404`/`422` |
| `GET` | `/api/v1/bookings/{id}/history` | Transition-log `200` `{entries:[…]}` по возрастанию `at` |

**`booking` в ответе:** `{id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, created_at}`.

Ошибки: `404` (нет брони/event_type), `409` (слот недоступен / лимит / гонка / reschedule cancelled или занятого хоста), `422` (плохая tz, прошедшее время, нет фильтра списка).

## 7. Тестирование

- **Конкурентность (крукс):** две параллельные `POST /bookings` на один слот у одно-хостового event_type (`asyncio.gather`, две сессии) → ровно один `201`, второй `409`. Двойная бронь физически невозможна.
- **Создание happy-path:** `201`, хост из свободного пула; после брони слот больше не в `GET /slots` (занятость + буферы).
- **getLuckyUser:** 2 хоста → 1-я бронь наименее загруженному, 2-я — другому; тай-брейк «наименее недавно назначенному».
- **Лимиты:** `booking_count=N` → `N+1` → `409`; `booking_duration` аналогично; граница периода в tz хоста (бронь после локальной полуночи — в следующем дне).
- **Буферы:** соседний слот в пределах буфера после брони не предлагается.
- **Cancel:** освобождает слот (пере-бронь того же слота `201`; слот снова в `/slots`); повторный cancel идемпотентен.
- **Reschedule:** тот же хост на свободный слот `200`; на слот, где хост занят, `409`; исключает свою же занятость; цепочка `created→rescheduled→cancelled` в `GET /history`.
- **min_notice** на создании и переносе.
- **Unit (чистое, без БД):** `assignment.py` (сортировка предстоящих + тай-брейк) и `limits.py` (границы периода в tz хоста) — табличные.

## 8. Открытые вопросы к следующим срезам (не блокируют срез 3)

1. **Интеграция с контуром `events`** (срез 4): публикация `booking.lifecycle` CloudEvents (created/rescheduled/cancelled) → event-booking (чат/Jitsi/напоминания) + event-saver (проекции). Точка — те же места, где пишется `booking_change_log`.
2. **Reservations / hold слота** (срез 4, если Booker-UX потребует «удержания» на время заполнения формы).
3. **`BusyInterval` без атрибуции пользователя** — сейчас обходим per-host-вызовом; при батч-оптимизации (один вызов на все user_ids) понадобится атрибуция.
4. **Клиент как пре-существующий user** — сейчас `client_user_id` передаёт вызывающий; в срезе 4 ingress/Booker резолвит/создаёт пользователя в event-users до брони.

## 9. Определение готовности среза 3

- Миграция `0002` (btree_gist + `booking` + `booking_change_log` + exclusion/CHECK/индексы) применяется; downgrade корректен.
- Модуль `booking/` реализован; эндпоинты §6 работают под `require_api_key`, покрыты тестами §7 (включая конкурентный).
- `BookingBusyTimesSource` заменил stub в DI (REQUEST-scope); `GET /slots` теперь исключает занятость + буферы.
- `assignment.py` и `limits.py` — чистые, unit-покрыты.
- Полный `pytest` зелёный, Ruff clean; `event-scheduling/CLAUDE.md` + `docs/` обновлены (booking-эндпоинты, модуль, замыкание seam'а); корневой `docs/architecture/ARCHITECTURE.md` — срез 3 в roadmap.
