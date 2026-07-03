# event-scheduling — доменная модель расписаний (срез 1)

> Дизайн-спек. Дата: 2026-07-03. Автор диалога: brainstorming.
> Часть большого замысла «полная замена внешнего CRM (форк cal.com) на собственную систему бронирования внутри монорепозитория `events`».

## 0. Контекст и рамка

**Большая цель:** cal.com (форк `~/PycharmProjects/calendar`, он же «внешний CRM») в итоге отключается; `events` сам владеет расписаниями, хостами, event-types, слотами и бронированиями. Идём инкрементально.

**Декомпозиция полной замены на срезы** (каждый — свой спек → план → реализация):
1. **Доменная модель расписаний** ← *этот спек*.
2. Движок расчёта слотов (read-side, CPU-bound, кандидат на Go).
3. Write-side бронирования (создание брони, таблица `booking`).
4. Booker UI (клиентский выбор слота).
5. Calendar-sync (внешние busy-times) — только если понадобится (сейчас отложено).
6. CRUD расписаний в ЛК организатора.

**Размещение:** всё внутри монорепозитория `events`. Новый сервис `event-scheduling` по вашим конвенциям.

**Ключевые решения по скоупу (YAGNI), зафиксированные в диалоге:**
- Командные события с распределением **round-robin** — с самого начала. Collective и managed — **не нужны**.
- Round-robin = **простая ротация / наименее загруженный**. Весов/приоритетов/host-групп **нет**.
- **Одно расписание на организатора** (пока).
- **Date overrides** — нужны. **Travel schedules** — нужны.
- **Десятки event types** с разной длительностью/буферами/лид-таймом.
- **Настраиваемые лимиты** бронирований — нужны.
- Внешние календари (Google/Office busy-times) — **не сейчас**; заложить точку расширения (seam), не реализуя.
- Данные из cal.com переносим **одноразовым ETL**; cal.com потом отключается.
- Схема — **чистый редизайн под суженный домен** (Подход 2), не верный порт cal.com.

**Опорный документ:** `~/PycharmProjects/calendar/docs/architecture/schedule-generation.md` — карта того, как cal.com определяет расписания и считает слоты (ссылки на код `путь:строка`). Ниже ссылки на его разделы.

---

## 1. Форма сервиса и границы (секция A)

**`event-scheduling`** — новый сервис в `events`:
- Python 3.14 / FastAPI / Dishka DI / `adapters/sql.py` (`SqlExecutor`, raw `text()` SQL) / `interfaces/` (Protocol) / frozen-dataclass DTO / alembic для собственной БД / Ruff (line 120) / pre-commit.
- Соблюдает стиль репозитория: **no `elif`**, **avoid `else`** (early returns / guard clauses / mapping dicts).
- Своя БД `event_scheduling` на общем postgres.
- В `docker-compose.services.yml` — новый сервис + БД; host-порт (предварительно **8004**); запись в корневую таблицу портов `CLAUDE.md`.

**Владение данными:**
- **Владеет таблицами:** `schedule`, `weekly_hours`, `date_override`, `travel_schedule`, `event_type`, `host`, `booking_limit`.
- **Не владеет личностью организатора.** Организаторы живут в `event-users` (их синкает `event-db-sync`: cal.com `"users"` → организатор). Поэтому `owner_user_id` / `host.user_id` — **непрозрачные UUID-ссылки** на `event-users` (как `participants.user_id`), без кросс-сервисных JOIN. Имя/почта резолвятся через `/api/users/by-ids` при необходимости.
- **Таймзона живёт на `schedule`** (обязательная), не на пользователе — убирает nullable-цепочку фолбэков cal.com (`Schedule.timeZone ?? User.timeZone`, раздел 5.1 документа) и кросс-сервисную зависимость. `travel_schedule` переопределяет tz на диапазонах дат.

**Границы среза 1:**
- **Входит:** определение расписаний + CRUD над перечисленными таблицами; ETL из cal.com.
- **Не входит:** таблица `booking` и запись броней (срез 3), внешние календари (срез 5), движок слотов (срез 2), ЛК-редактор (срез 6).
- Занятость представлена только **seam'ом** `BusyTimesSource` (Protocol) со stub-реализацией `→ []`, чтобы срезу 2 было куда подключиться.

**Событийная эмиссия:** CloudEvents об изменениях расписаний **не эмитим** пока (YAGNI). Слот-движок читает БД напрямую (стратегия S1 из раздела 8 документа), других потребителей нет. Добавим, когда появится потребитель.

---

## 2. Схема БД (секция B, Подход 2)

Общее: PK — `uuid`; `created_at`/`updated_at timestamptz`. Времена суток — `time` (локальны к эффективной таймзоне); даты — `date`. Диапазоны хранятся как в cal.com; UTC-математика — задача среза 2, не хранилища.

### 2.1 `schedule` — одно на организатора
```
id            uuid pk
owner_user_id uuid  not null   -- ссылка на event-users; UNIQUE (пока одно расписание на человека)
name          text  not null
time_zone     text  not null   -- IANA, обязательна (без nullable-фолбэка cal.com)
```

### 2.2 `weekly_hours` — недельные рабочие часы (recurring)
Одна строка = один интервал в один день недели; несколько строк на день = разрывные смены.
```
id          uuid pk
schedule_id uuid not null  fk -> schedule (on delete cascade)
day_of_week smallint not null  -- 1..7, 1 = понедельник … 7 = воскресенье (ISO-8601 / isoweekday)
start_time  time not null
end_time    time not null      -- CHECK end_time > start_time
```
> Кодировка дня — ISO (1=Пн), отличается от cal.com (`days[]`, 0=Вс). ETL делает ремап.

### 2.3 `date_override` — переопределения на конкретную дату
Полностью заменяют часы дня. Несколько строк на дату = несколько окон. Одна строка NULL/NULL = выходной.
```
id          uuid pk
schedule_id uuid not null  fk -> schedule (cascade)
date        date not null
start_time  time null          -- окно доступности на эту дату
end_time    time null          -- обе NULL => дата целиком заблокирована; CHECK (обе NULL) OR (обе заданы AND end>start)
```

### 2.4 `travel_schedule` — переопределение таймзоны на диапазоне дат
```
id             uuid pk
schedule_id    uuid not null  fk -> schedule (cascade)
time_zone      text not null
start_date     date not null
end_date       date null       -- открытый диапазон
prev_time_zone text null       -- куда вернуться после поездки
```

### 2.5 `event_type` — бронируемое событие (round-robin, десятки)
```
id                         uuid pk
slug                       text not null   -- в URL; UNIQUE
title                      text not null
scheduling_type            text not null default 'round_robin'  -- enum-точка расширения (collective — потом)
duration_minutes           int  not null
slot_interval_minutes      int  null       -- гранулярность нарезки; NULL => = duration_minutes
min_booking_notice_minutes int  not null default 0  -- лид-тайм
buffer_before_minutes      int  not null default 0
buffer_after_minutes       int  not null default 0
```

### 2.6 `host` — членство организатора в round-robin пуле события
Минимально (без weight/priority/groupId).
```
event_type_id uuid not null  fk -> event_type (cascade)
user_id       uuid not null  -- ссылка на event-users
schedule_id   uuid not null  fk -> schedule  -- расписание этого хоста
primary key (event_type_id, user_id)
```
> Справедливость round-robin («наименее загруженный») считается в срезах 2/3 из счётчика броней — полей в схеме не требует.

### 2.7 `booking_limit` — настраиваемые лимиты
Нормализация JSON cal.com (`bookingLimits`/`durationLimits`) в строки.
```
id            uuid pk
event_type_id uuid not null  fk -> event_type (cascade)
limit_type    text not null   -- 'booking_count' | 'booking_duration'
period        text not null   -- 'day' | 'week' | 'month' | 'year'
value         int  not null   -- count (шт) или duration (минуты); CHECK value > 0
unique (event_type_id, limit_type, period)
```

### 2.8 Seam занятости (не таблица)
`interfaces/busy_times.py`:
```python
class BusyTimesSource(Protocol):
    def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]: ...
```
Срез 1: stub-реализация `→ []`. Срез 3: реализация поверх таблицы `booking`. Срез 5 (опц.): реализация поверх внешних календарей. Множественные источники объединяются на стороне движка (срез 2).

### 2.9 Отброшено из cal.com (относительно раздела 1 документа)
Перегруженная `Availability`; nullable `Schedule.timeZone`; `Host.weight/priority/groupId`; `HostGroup`; `SelectedCalendar`/`CalendarCacheEvent`; collective/managed/restrictionSchedule/instantMeeting; инлайн-`availability` на event/user.

---

## 3. CRUD-контракты и семантика сохранения (секция C)

Тонкие FastAPI-роуты → сервисный слой → `SqlExecutor`. DTO — frozen dataclasses.

### 3.1 Расписание организатора (одно на человека)
- `GET /schedules/{owner_user_id}` → `{schedule, weekly_hours[], date_overrides[], travel_schedules[]}` одним DTO (аналог atom cal.com).
- `PUT /schedules/{owner_user_id}` — **replace-all в одной транзакции**: `DELETE` всех `weekly_hours`+`date_override` расписания → `INSERT` нового набора (паттерн `ScheduleService.update`, раздел 2.2 документа: на каждое сохранение весь набор availability перезаписывается целиком). `time_zone`/`name` апдейтятся тут же.
- `PUT /schedules/{owner_user_id}/travel` — отдельный путь для `travel_schedule` (diff-и-заменить, как cal.com через профиль, раздел 2.3), чтобы не переписывать поездки при каждом сохранении сетки.

### 3.2 Event types + hosts + limits
- `GET /event-types`, `GET /event-types/{id}` (с `hosts[]`, `booking_limits[]`).
- `POST /event-types`, `PUT /event-types/{id}`, `DELETE /event-types/{id}`.
- Вложенные `hosts` и `booking_limits` редактируются в `PUT /event-types/{id}` тем же replace-all в транзакции.

### 3.3 Краевые проверки (валидация на write)
1. `time_zone` — валидная IANA-зона (иначе 422).
2. `weekly_hours`: `end_time > start_time`. **Полуночный край не поддерживаем** в срезе 1 (нет в текущем cal.com-использовании).
3. `date_override`: либо обе `start/end` заданы (`end > start`), либо обе NULL (выходной). Смешанное — 422.
4. `booking_limit`: `value > 0`. Иерархию периодов (день ≤ неделя ≤ месяц ≤ год) **не** валидируем в срезе 1 — это проверка этапа брони (срез 3).
5. `host.schedule_id` принадлежит `host.user_id`; `owner_user_id` уникален в `schedule`.
6. Пересечение интервалов внутри дня (`weekly_hours`) **разрешаем** (cal.com объединяет их в движке) — не валидируем.

### 3.4 Ошибки
`ApiError`-стиль: 404 (нет расписания/event-type), 422 (валидация + детали), 409 (нарушение уникальности `owner_user_id`). Все write — в транзакции; любая ошибка → полный откат.

### 3.5 Аутентификация / RBAC
Клиентского ЛК-редактора в срезе 1 **нет** (срез 6). Источник write — ETL и, при необходимости, admin. Сервис за внутренним периметром (как `event-admin` ↔ `event-users`); внешний RBAC — вне среза 1.

---

## 4. ETL-миграция из cal.com (секция D)

Одноразовый идемпотентный скрипт (напр. `event-scheduling/scripts/etl_from_calcom.py`): читает БД `calcom` (read-only), пишет в `event_scheduling`. Запуск вручную при отсечке; повторный прогон — upsert по стабильным ключам (без дублей). Отказоустойчив к строке: плохая строка → лог в отчёт + пропуск, прогон не падает.

**Резолв личности:** `calcom users.id` (int) → `event-users` UUID **по email** (организаторы уже в `event-users`). Email не найден → лог-пропуск.

| calcom | → event_scheduling | Преобразование |
|---|---|---|
| `Schedule` | `schedule` | `time_zone = Schedule.timeZone ?? User.timeZone`; `owner_user_id = map(userId)`. Несколько расписаний у пользователя → берём `defaultScheduleId`, **остальные пропускаем с логом**. |
| `Availability` (recurring: `days` непустой, `date=null`) | `weekly_hours` | Разворот `days Int[]` в строки по дню; **ремап `calcom_day(0=Вс) → iso(1=Пн..7=Вс)`**; `startTime/endTime → time`. |
| `Availability` (override: `date` задана, `days=[]`) | `date_override` | `date`, `start_time/end_time`; cal.com «выходной» → строка NULL/NULL. |
| `TravelSchedule` | `travel_schedule` | Прямой перенос; привязка к `schedule_id` организатора. |
| `EventType` (round-robin; managed/collective — пропуск) | `event_type` | `slug/title/duration → duration_minutes`; буферы/лид-тайм/`slotInterval`. |
| `Host` (для перенесённых event_type) | `host` | `(eventTypeId, userId)` → UUID-ссылки; `schedule_id` = default-расписание хоста; **weight/priority/groupId отбрасываем**. |
| `EventType.bookingLimits`/`durationLimits` (JSON) | `booking_limit` | Разворот JSON `{day/week/month/year: N}` → строки `(limit_type, period, value)`. |

**Не переносим:** `SelectedCalendar`/`CalendarCacheEvent`, `HostGroup`, `restrictionSchedule`, инлайн-`availability`, веса, collective/managed event types, брони (`Booking` — срез 3).

**Отчёт о миграции:** в конце — сводка «перенесено/пропущено» по каждой таблице **с причинами** (email не найден, не-RR событие, лишнее расписание). Никаких тихих потерь.

**Фикстуры теста ETL:** прогон на `docker/calcom-init/` (детерминированный сид cal.com в dev).

---

## 5. Тестирование и обработка ошибок (секция E)

Стек: `pytest`, Ruff. Покрытие по слоям.

1. **Валидация write** (unit): IANA-tz; `end > start`; `date_override` NULL-инвариант; `booking_limit.value > 0`; `host.schedule_id` принадлежит `host.user_id`; уникальность `owner_user_id`.
2. **Replace-all транзакция** (integration, тестовая БД): `PUT /schedules/{id}` полностью перезаписывает `weekly_hours`+`date_override`; ошибка в середине откатывает всё; `travel_schedule` не трогается.
3. **Event-type + вложенные hosts/limits** (integration): вложенный replace-all, каскадное удаление.
4. **ETL-маппинг** (integration на `docker/calcom-init/`): ремап дня `0=Вс → 1..7`; резолв email→UUID; `timeZone ?? user.timeZone`; выбор `defaultScheduleId` + лог-пропуск лишних; разворот `bookingLimits` JSON → строки; отчёт с причинами пропусков.
5. **`BusyTimesSource` stub**: возвращает `[]`; интерфейс стабилен для среза 2.

**Рантайм-ошибки:** `ApiError`-стиль (404/422/409); все write в транзакции с полным откатом; ETL отказоустойчив к строке.

---

## 6. Открытые вопросы к следующим срезам (не блокируют срез 1)

1. **Где живёт round-robin-выбор** («наименее загруженный»)? Вызывается и на read (квалификация слотов, срез 2), и на write (финальное назначение, срез 3). Решить: общий пакет-библиотека vs дублирование чтения. (В cal.com это `getLuckyUser`, раздел 3.4 документа.)
2. **Политика cache-miss / eventual consistency** — актуализируется в срезе 5 (внешние календари), если он вообще понадобится.
3. **Единый контракт слотов** для среза 2 (в cal.com два транспорта — tRPC и `/v2/slots` поверх одного движка; раздел 6.6). При выделении read-сервиса свести к одному публичному API.
4. **ETL броней** (`Booking`) — отдельный ETL в срезе 3, если история нужна.

---

## 7. Определение готовности среза 1

- Сервис `event-scheduling` поднимается в docker-compose со своей БД `event_scheduling` и alembic-миграциями всех 7 таблиц.
- CRUD-эндпоинты (§3) работают, покрыты тестами (§5), проходят Ruff.
- ETL-скрипт (§4) переносит `docker/calcom-init/` сид без падений, печатает отчёт, покрыт интеграционным тестом.
- `BusyTimesSource` Protocol + stub на месте.
- Обновлены: корневой `CLAUDE.md` (таблица сервисов + портов), `event-scheduling/CLAUDE.md`, `docs/architecture/` (топология системы — новый сервис).
