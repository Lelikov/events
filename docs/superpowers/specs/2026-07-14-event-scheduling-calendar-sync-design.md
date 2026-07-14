# event-scheduling — calendar-sync (внешние busy-times, iCal URL) — срез 5

> Дизайн-спек. Дата: 2026-07-14.
> event-scheduling начинает вычитать из доступности **занятость внешнего календаря хоста** (подписка по iCal URL), чтобы слот-движок и booking-create не предлагали/не пускали конфликтные времена. Аддитивно — существующий код слот-движка и booking-create НЕ меняем (тот же `BusyTimesSource` seam).

## 0. Контекст и рамка

**Пивот:** `BusyTimesSource` Protocol (`interfaces/busy_times.py`): `get_busy(user_ids, window) -> list[BusyInterval]`. Сейчас единственная реализация — `BookingBusyTimesSource` (`booking/busy_source.py`, читает таблицу `booking` с буферами). Слот-движок (`slots/service.py`) и booking-create (`booking/service.py`) вычитают busy-интервалы из доступности. Calendar-sync добавляет **второй источник — внешний календарь** — и объединяет их через `CompositeBusyTimesSource`, так что оба потребителя получают внешнюю занятость **без изменения своего кода**.

**Направление (утверждено): import** — внешний календарь → блокирует слоты. Read-only, без OAuth write-scope. **Провайдер первого среза (утверждено): A — iCal URL-подписка** (Google/Office/Apple экспортируют секретный `.ics` feed). Export (бронь→календарь), OAuth-провайдеры (Google/Office), CalDAV — **вне рамок** (§8).

**Границы среза 5:** только event-scheduling. Хранилище подключений + кэш busy-событий; фоновый поллер фетчит/парсит `.ics`; `CompositeBusyTimesSource` вычитает внешнюю занятость; management-эндпоинты (под `require_api_key`) для подключить/список/удалить/синк. Слот-движок, booking-create, cal.com-путь, event-booking, другие сервисы — **не трогаем**.

### Ключевые факты (разведка, сверено с кодом)
- `host_user_id` — «голый» UUID пользователя (event-users), без локального FK (как `host.user_id`, `schedule.owner_user_id`). Календарь подключается по `host_user_id`.
- **Занятость через Protocol.** `BusyTimesSource.get_busy(user_ids, window)`. `BookingBusyTimesSource.get_busy` добавляет опциональный `exclude_booking_id` (использует booking-create). Composite должен принять тот же опциональный параметр и пробросить только в booking-источник.
- **Фоновый поллер — established pattern.** `main.py` lifespan уже запускает `run_dispatcher_loop` (outbox) + `run_reminder_loop` (4a.3) на общем `asyncio.Event stop`. Добавляем третий: `run_calendar_sync_loop`.
- Миграции head — `0004`. Новая — `0005`. Монорепо-конвенции: Python 3.14, uv, FastAPI, Dishka, SqlExecutor (raw `:param`), Ruff 120, frozen DTO, **без elif/избегать else**, `interfaces/` протоколы.
- Слот-движок кап окна — 62 дня (`slots.py::_MAX_WINDOW_DAYS`). Кэш-окно синка совпадает (≤62 дня).

## 1. Архитектура

```
main.py lifespan
  ├── run_dispatcher_loop  (outbox)         — существующий
  ├── run_reminder_loop    (reminders)      — 4a.3
  └── run_calendar_sync_loop                — НОВЫЙ
        every CALENDAR_SYNC_INTERVAL:
          for cal in enabled external_calendar rows:
            bytes = ical_client.fetch(cal.url)                    # httpx GET .ics
            busy  = ical_parser.expand(bytes, [now, now+window])  # icalendar + recurring_ical_events
            replace external_calendar_event cache for cal (delete+insert in txn)
            update cal.last_synced_at / last_error

GET /slots, POST /bookings  →  BusyTimesSource (DI)  →  CompositeBusyTimesSource
                                                          ├── BookingBusyTimesSource      (booking table)
                                                          └── ExternalCalendarBusyTimesSource (cache table)
```

**Модули (`event_scheduling/calendar/`, новый пакет):**
- `calendar/dto.py` — `ExternalCalendarDTO`, `BusyEvent` (frozen).
- `calendar/interfaces.py` — `ICalendarReadAdapter`, `ICalendarWriteAdapter`, `IICalClient`, `IICalParser` (Protocols).
- `calendar/ical_client.py` — httpx-фетч `.ics` (timeout, non-200 → error, http(s)-only).
- `calendar/ical_parser.py` — `expand(ics_bytes, window) -> list[BusyEvent]` (RRULE-раскрытие, all-day, DST, skip TRANSPARENT/CANCELLED).
- `calendar/read_adapter.py` — `list_enabled()`, `list_by_host(host_user_id)`, `get(id)`.
- `calendar/write_adapter.py` — `create(host_user_id, url)`, `delete(id)`, `replace_cache(calendar_id, events)`, `mark_synced(id, now)`, `mark_error(id, now, err)`.
- `calendar/busy_source.py` — `ExternalCalendarBusyTimesSource(sql).get_busy(user_ids, window)` (кэш-overlap).
- `calendar/composite_busy.py` — `CompositeBusyTimesSource(booking, external).get_busy(user_ids, window, exclude_booking_id=None)`.
- `calendar/sync_service.py` — `sync_calendar(sql, client, parser, clock, cal_row, window_days)`.
- `calendar/dispatcher.py` — `run_calendar_sync_loop(sessionmaker, client, parser, clock, *, interval_s, window_days, stop)`.
- `routers/calendar.py` — management API (под `require_api_key`).
- `schemas/calendar.py` — Pydantic request/response.
- Правки: `config.py` (настройки), `ioc.py` (провайдеры + композитный busy), `main.py` (3-й фоновый task), `alembic/versions/0005_external_calendar.py`, `pyproject.toml` (deps).

## 2. Схема БД: миграция `0005_external_calendar`

```sql
CREATE TABLE external_calendar (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    host_user_id  UUID NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'ical_url',
    url           TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    last_synced_at TIMESTAMPTZ NULL,
    last_error    TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_external_calendar_kind CHECK (kind IN ('ical_url')),
    CONSTRAINT uq_external_calendar_host_url UNIQUE (host_user_id, url)
);
CREATE INDEX ix_external_calendar_enabled ON external_calendar (host_user_id) WHERE enabled;

CREATE TABLE external_calendar_event (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calendar_id  UUID NOT NULL REFERENCES external_calendar(id) ON DELETE CASCADE,
    busy_start   TIMESTAMPTZ NOT NULL,
    busy_end     TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_ext_cal_event_range CHECK (busy_end > busy_start)
);
CREATE INDEX ix_ext_cal_event_window ON external_calendar_event (calendar_id, busy_start, busy_end);
```
- `external_calendar` — подключения; `uq(host_user_id, url)` не даёт дублей; партиал-индекс по enabled для поллера.
- `external_calendar_event` — **кэш** раскрытой занятости; `ON DELETE CASCADE` от календаря. Синк = delete+insert всех строк календаря в одной транзакции (окно ограничено ≤62 дня, объём мал). Нет `uid`/дедупа — полная замена per-calendar per-tick проще и корректна.
- **DATA_MODEL.md:** 2 новые таблицы (теперь 13 в event-scheduling DB).

## 3. Фетч + парс (.ics → busy-интервалы)

- **`ical_client.fetch(url) -> bytes`** (httpx): GET с `CALENDAR_FETCH_TIMEOUT`, follow_redirects, non-2xx → `UpstreamError`; **только `http`/`https` схема** (иначе `ValidationError`). SSRF-хардненинг (блок приватных IP) — §8 (deferred), т.к. эндпоинты под admin-auth.
- **`ical_parser.expand(ics_bytes, window: TimeWindow) -> list[BusyEvent]`** (`icalendar` парсит, `recurring_ical_events` раскрывает VEVENT в `[window.start, window.end]`):
  - Пропускать `TRANSP:TRANSPARENT` (свободно) и `STATUS:CANCELLED`.
  - All-day (VALUE=DATE) → busy на весь день в UTC (как cal.com).
  - Нет DTEND → DTSTART + DURATION, иначе 0-длит. пропустить.
  - Клиппинг к окну; datetime приводить к UTC-aware.
  - Возврат: `list[BusyEvent(busy_start, busy_end)]` (UTC).
- Парсер — **чистая функция** (bytes+window → интервалы), максимально тестируемая на фикстурах.

## 4. Синк (поллер + кэш)

- **`sync_calendar(sql, client, parser, clock, cal_row, window_days)`:** window = `[now, now+window_days]`; `bytes = client.fetch(cal_row.url)`; `events = parser.expand(bytes, window)`; `write.replace_cache(cal_row.id, events)`; `write.mark_synced(cal_row.id, now)`. При исключении (fetch/parse) → `write.mark_error(cal_row.id, now, str(exc))` и **кэш НЕ трогаем** (сохраняем последний успешный снимок).
- **`run_calendar_sync_loop(...)`** — зеркалит `run_reminder_loop`: пер-tick своя сессия, `list_enabled()`, синк каждого календаря в изоляции (try/except на один календарь → лог + продолжить), `commit`; переживает падение тика; прерываемый сон на общем `stop`.
- **Кэш свежести:** задержка = период поллера (`CALENDAR_SYNC_INTERVAL`, дефолт 300с). `GET /slots` читает кэш (быстро, без внешнего HTTP в request-path).

## 5. Composite busy-source и DI

- **`ExternalCalendarBusyTimesSource(sql).get_busy(user_ids, window)`** — SQL: join `external_calendar` (enabled ∧ `host_user_id = ANY(:users)`) → `external_calendar_event`, overlap `tstzrange(busy_start, busy_end) && tstzrange(:lo, :hi)` → `list[BusyInterval]`.
- **`CompositeBusyTimesSource(booking, external).get_busy(user_ids, window, exclude_booking_id=None)`** — `await booking.get_busy(user_ids, window, exclude_booking_id) + await external.get_busy(user_ids, window)`. Реализует `BusyTimesSource`; принимает опциональный `exclude_booking_id` (booking-create его передаёт; слот-движок — нет).
- **DI (`ioc.py`):** `provide_busy_source` теперь возвращает `CompositeBusyTimesSource(BookingBusyTimesSource(sql), ExternalCalendarBusyTimesSource(sql))` (REQUEST scope). Слот-движок и booking-create зависят от `BusyTimesSource` — **их код и провайдеры не меняются**, только фабрика busy-source.

## 6. Management API (под `require_api_key`)

Под `/api/v1/calendars` (Bearer `SCHEDULING_API_KEY`, как остальные `/api/*`):
- **`POST /api/v1/calendars`** body `{host_user_id: UUID, url: str}` → создать (`kind='ical_url'`, `enabled=true`) → 201 `{id, host_user_id, kind, url, enabled, last_synced_at, last_error}`. Валидация: url http(s); дубль (host_user_id,url) → 409.
- **`GET /api/v1/calendars?host_user_id=`** → `{items:[...]}`.
- **`DELETE /api/v1/calendars/{id}`** → 204 (кэш каскадом).
- **`POST /api/v1/calendars/{id}/sync`** → синхронно `sync_calendar(...)` один раз → 200 c `{last_synced_at, last_error}` (для ручного «проверить сейчас»; ЛК-редактор среза 6 / admin потом дёргают).
- Не публичные (booker'у управление календарями не нужно).

## 7. Обработка ошибок
- Fetch недоступен / non-2xx / таймаут / битый `.ics` → `sync_calendar` ловит, `mark_error`, кэш сохраняется (последний успешный снимок продолжает блокировать слоты). Поллер переживает падение тика.
- Пустой/отсутствующий кэш (никогда не синкался или всё удалено) → `get_busy` вернёт `[]` → внешняя занятость просто не вычитается (fail-open: лучше показать слот, чем упасть; конфликт всё равно ловится exclusion-constraint'ом при create, если бронь реально пересекается с другой бронью — но НЕ с внешним событием). Это принятый компромисс для read-side.
- `POST /calendars/{id}/sync` при недоступном feed → 200 с `last_error` (не 5xx — операция «попытка синка» выполнена).

## 8. Отложено / вне рамок
1. **OAuth-провайдеры (Google/Office)** — токены, refresh, freebusy API, вебхуки → срез 5.2.
2. **Export** (бронь → внешний календарь) — нужен write-scope → отдельный срез.
3. **CalDAV** — PROPFIND/REPORT → позже.
4. **SSRF-хардненинг** — блок приватных/loopback IP при фетче (сейчас только http(s)-схема; эндпоинты под admin-auth). Отдельный hardening.
5. **Вебхуки/пуш-обновления** — сейчас только поллинг; провайдерские webhooks — с OAuth-срезом.
6. **Дедуп событий по UID / инкрементальный синк** — сейчас полная замена per-calendar; при больших календарях оптимизировать позже.
7. **UI подключения календаря** — management API готов; UI в ЛК-редакторе (срез 6) или admin.

## 9. Тестирование
- **`ical_parser.expand`** (unit, фикстурные `.ics`): одиночное событие; recurring (RRULE weekly) раскрыт в окне; all-day; событие через DST; `TRANSP:TRANSPARENT` пропущено; `STATUS:CANCELLED` пропущено; нет DTEND; клиппинг к окну.
- **`ical_client.fetch`** (unit, httpx.MockTransport): 200→bytes; non-2xx→UpstreamError; не-http(s) url→ValidationError.
- **`sync_calendar`** (integration DB + fake client/parser): success → кэш заменён + `last_synced_at`; ошибка fetch → `last_error`, кэш сохранён.
- **`ExternalCalendarBusyTimesSource.get_busy`** (integration): overlap-запрос возвращает busy для host; вне окна/чужой host — нет; disabled-календарь исключён.
- **`CompositeBusyTimesSource`** (unit, fakes): union бронь+внешний; `exclude_booking_id` проброшен только в booking-источник.
- **Management router** (integration): create (201; дубль→409; не-http(s)→422), list, delete (204+каскад), sync (200 с last_error на сбое).
- **e2e (integration)**: засиженный external_calendar_event, пересекающий слот → `GET /api/v1/slots` НЕ содержит этот слот; booking-create на внешне-занятое время → отклонён/недоступен (слот не в доступных).
- Полный `pytest` (Docker PG) + ruff clean; слот-движок/booking-create/reminders/outbox не сломаны.

## 10. Config (event-scheduling `Settings`)
- `calendar_sync_enabled: bool = True` — рубильник (не запускать поллер).
- `calendar_sync_interval_seconds: float = 300.0`.
- `calendar_sync_window_days: int = 62` (= кап слот-окна).
- `calendar_fetch_timeout_seconds: float = 15.0`.
- compose/env: дефолтов достаточно; переопределяемы через `.env`.

## 11. Определение готовности среза 5
- Миграция 0005 (`external_calendar` + `external_calendar_event`); `pyproject` deps (`icalendar`, `recurring-ical-events`).
- Парсер (RRULE/all-day/DST/skip), клиент (http(s), timeout), синк-сервис (replace-cache + mark), поллер (3-й lifespan task, `CALENDAR_SYNC_ENABLED` рубильник).
- `ExternalCalendarBusyTimesSource` + `CompositeBusyTimesSource` + DI-бинд; слот-движок и booking-create вычитают внешнюю занятость без правок их кода.
- Management API (create/list/delete/sync) под `require_api_key`.
- Тесты §9 зелёные; ruff clean; ничего существующее не сломано.
- Docker/compose env + доки: `event-scheduling/CLAUDE.md` (модуль calendar + поллер + рубильник), `docs/DATA_MODEL.md` (2 таблицы), `docs/SERVICE_OVERVIEW.md` (срез 5 delivered), корневой `docs/architecture/ARCHITECTURE.md` (внешний busy-источник).
