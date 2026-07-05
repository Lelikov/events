# event-scheduling — движок расчёта слотов (срез 2)

> Дизайн-спек. Дата: 2026-07-05.
> Второй срез замены внешнего CRM (форк cal.com): read-side расчёт доступных слотов поверх доменной модели среза 1.

## 0. Контекст и рамка

**Большая цель** (без изменений): cal.com отключается; `events` сам владеет расписаниями, слотами и бронированиями. Идём инкрементально.

**Срезы:** (1) доменная модель расписаний — **готово, в main**; (2) **движок слотов** ← *этот спек*; (3) write-side бронирования (таблица `booking`); (4) Booker UI; (5) calendar-sync (отложено); (6) ЛК-редактор.

**Опорные документы:**
- Модель среза 1: `docs/superpowers/specs/2026-07-03-event-scheduling-domain-model-design.md`; сервис `event-scheduling/` (8 таблиц, `CLAUDE.md`, `docs/DATA_MODEL.md`).
- Алгоритм cal.com: `~/PycharmProjects/calendar/docs/architecture/schedule-generation.md` §4 (движок), §5 (таймзоны/DST).
- Seam занятости: `event-scheduling/event_scheduling/interfaces/busy_times.py` — `BusyTimesSource.get_busy(user_ids, window) -> list[BusyInterval]`, сейчас `StubBusyTimesSource → []`.

**Ключевые решения (зафиксированы в диалоге):**
- **Язык — Python** (не Go). Обоснование по замеренному масштабу (ниже §1).
- **Размещение — внутри `event-scheduling`**, изолированным модулем `slots/` (не новый сервис). Read-side и domain-model со-локализованы; читает ту же БД (S1); отдельное масштабирование не нужно при единицах RPS.
- **Граница среза 2:** полный конвейер «расписание → UTC-диапазоны → слоты» с рабочими часами, date-overrides, travel-tz, `min_booking_notice`, round-robin-объединением хостов. **Занятость** (`BusyTimesSource`) и **лимиты** (`booking_limit`) подключаются через **пустые seam'ы** — данные появятся в срезе 3.
- **Кэш результата — нет** в срезе 2 (единицы RPS; YAGNI).

## 1. Масштаб и выбор языка

Замеренные порядки величин: до **нескольких сотен хостов** на round-robin событие; окно расчёта ~**месяц**; слоты **30–60 мин**; **единицы RPS** в пике; жёсткого latency-SLA нет («в разумных пределах»).

**Вывод — Python по конвенциям монорепо.** При единицах RPS пропускная способность нетривиальна; latency достижима аккуратной реализацией (целочисленная арифметика минут, предрасчёт tz-смещения на день, без per-slot аллокаций). Тормоза cal.com шли от живых вызовов внешних календарей (убраны — отложены) и `dayjs`-аллокаций на слот (не воспроизводим). Go дал бы latency-выигрыш ценой третьего языка в монорепо — не оправдано этими числами.

**Страховка:** чистое ядро (`slots/domain.py` + `slots/timezones.py`) — нулевой IO, извлекаемо в Go/Rust позже без переписывания сервиса, если упрёмся в таргет.

## 2. Контракт API

Read-only эндпоинт под существующим `require_api_key` (внутренний периметр):
```
GET /api/v1/slots?event_type_id=<uuid>&start=<iso>&end=<iso>&time_zone=<iana>
```

**Вход:**
| Параметр | Тип | Обяз. | Смысл |
|---|---|---|---|
| `event_type_id` | uuid | да | какое событие; хосты round-robin берутся из его `host`-строк (клиент не передаёт) |
| `start`, `end` | ISO-8601 instant, **UTC** | да | окно расчёта; контракт держим в UTC (§5.5 документа) |
| `time_zone` | IANA | да | таймзона визитёра — **только** для группировки ответа по локальным дням |

- `duration_minutes` / `slot_interval_minutes` / `min_booking_notice_minutes` / буферы берутся **из `event_type`** (фикс на событие), клиент не передаёт.
- **Cap окна:** `end - start` > 62 дней → `422` (ограничивает worst-case: сотни хостов × диапазон).

**Выход** — слоты, сгруппированные по локальной дате визитёра; каждый слот — UTC ISO:
```json
{
  "event_type_id": "…",
  "time_zone": "Europe/Moscow",
  "slots": {
    "2026-10-01": ["2026-10-01T06:00:00Z", "2026-10-01T06:30:00Z"],
    "2026-10-03": ["2026-10-03T07:00:00Z"]
  }
}
```
- Дни без доступности **опускаются** (готовый вход для DatePicker среза 4).
- Слоты — UTC ISO; локальную конвертацию делает клиент. Группировку по дню сервер делает через `zoneinfo` один раз на слот (не тяжёлый tz-парсинг per-slot).

**Ошибки:** `404` (event_type не найден), `422` (невалидная IANA-tz, `end <= start`, окно > cap).

## 3. Конвейер расчёта (ядро)

Пять шагов, целочисленная арифметика минут (UTC-эпохи в минутах внутри).

**3.1 Batch-загрузка** (один префетч, аналог `calculateHostsAndAvailabilities`, §4.1 документа). По `event_type_id`:
- сам `event_type` (`duration_minutes`, `slot_interval_minutes`, `min_booking_notice_minutes`, `buffer_before/after_minutes`) → `404` если нет;
- его `host`-строки (`schedule_id`, `user_id`);
- для этих `schedule_id`: `weekly_hours`, `date_override`, `travel_schedule`, `schedule.time_zone`.

Загрузка пачками по спискам id (`WHERE schedule_id = ANY(:ids)`), без N+1.

**3.2 Per-host → UTC-диапазоны доступности над окном.** Для каждого локального дня окна:
- **эффективная tz** = `schedule.time_zone`, переопределённая `travel_schedule`, если дата ∈ `[start_date, end_date]` (open-ended при `end_date = NULL`);
- есть `date_override` на дату? да → его интервалы; строка с NULL/NULL → **день заблокирован** (пусто). нет override → `weekly_hours` по `day_of_week` (ISO 1=Пн…7=Вс);
- локальные `[start_time, end_time]` в эффективной tz на эту дату → **UTC-интервал**; **DST**: смещение вычисляется на конкретный день (как §5.3 документа), полночь не «плывёт»;
- вычитаем занятость из `BusyTimesSource.get_busy(host_user_ids, window)` (в срезе 2 → `[]`, вычитание — no-op).

**3.3 Round-robin агрегация — UNION по всем хостам.** Слот доступен, если свободен **≥1** хост. Групп/весов нет → одно объединение отсортированных интервалов (sorted-merge). *(Отличие от cal.com: там RR группируется по `groupId` и группы пересекаются; у нас групп нет — плоский union по пулу.)*

**3.4 Нарезка на слоты.** Шаг = `slot_interval_minutes` (или `duration_minutes`, если NULL). Кандидат `[T, T+duration]` эмитится, если:
- целиком влезает в интервал доступности (union из 3.3), и
- `T ≥ now + min_booking_notice_minutes` (где `now` — инжектируемые часы, UTC).

Буферы (`buffer_before/after`) **проплюблены, но инертны** в срезе 2: они блокируют время вокруг чужих броней, что активируется только с непустым `BusyTimesSource` (срез 3). Пустой пул броней их не задействует.

**3.5 Группировка** эмитированных UTC-слотов по локальной дате визитёра (через `time_zone`) → корзины ответа; пустые дни опускаются.

**Лимиты (`booking_limit`)** — seam: требуют счёта существующих броней, которых нет до среза 3. В срезе 2 счётчик = 0, лимиты не срезают слоты. Точка подключения — тот же префетч (лимиты уже грузятся), проверка добавится в срезе 3.

## 4. Структура модуля

Изолированный пакет `event_scheduling/slots/`:

| Файл | Ответственность | IO |
|---|---|---|
| `slots/domain.py` | **Чистое ядро**: интервальная математика (union хостов, subtract busy, нарезка, `min_notice`-отсечка, «влезает в интервал»). Целочисленные минуты. | нет |
| `slots/timezones.py` | tz/DST: эффективная tz на день (base + travel), локальное→UTC. `zoneinfo`. | нет |
| `slots/dto.py` | frozen DTO входа (`HostAvailability`, `EventTypeConfig`, `SlotWindow`) и результата (`SlotResult`). | нет |
| `slots/read_adapter.py` | `SlotsReadAdapter` — один batch-SQL (event_type + hosts + расписания) через `SqlExecutor` → DTO. | DB |
| `slots/service.py` | оркестрация: load → domain-пайплайн → группировка. Зависит от `ISlotsReadAdapter` + `BusyTimesSource` + инжектируемых часов. | склейка |
| `slots/interfaces.py` | `ISlotsReadAdapter`, `ISlotService` (переиспользуем `BusyTimesSource` из `interfaces/busy_times.py`). | — |

Плюс:
- `routers/slots.py` — `GET /api/v1/slots` (парс query → `ISlotService` → схема), под `require_api_key`.
- `schemas/slots.py` — Pydantic вход (query) / выход.
- `ioc.py` — REQUEST-scope провайдеры: `ISlotsReadAdapter → SlotsReadAdapter(sql)`, `BusyTimesSource → StubBusyTimesSource()`, `ISlotService → SlotService(...)`, плюс провайдер часов (Clock).
- `main.py` — `app.include_router(slots_router)`; тест-фикстура `app` в `conftest.py` тоже включает `slots_router`.

**Чистая граница:** `domain.py` + `timezones.py` — нулевой IO, извлекаемое ядро. Остальное — тонкая IO-оболочка.

**Детерминизм:** `now` инжектируется в `SlotService` (Clock / callable), не `datetime.now()` внутри, — иначе тесты `min_notice` недетерминированы. В проде провайдер возвращает `datetime.now(UTC)`; в тестах — фиксированное значение.

**Стиль:** те же конвенции сервиса — Python 3.14, Dishka, `SqlExecutor` raw `:param` SQL, frozen-dataclass DTO, Pydantic только в `schemas/`, **no `elif`/avoid `else`**, Ruff 120.

## 5. Тестирование и ошибки

- **Unit (чистые, без БД) — где живёт корректность:** union двух хостов (пересекающиеся / непересекающиеся часы); subtract busy (для будущего — стаб-интервалы); нарезка (шаг, «влезает целиком», отсечка по `now + notice`); **DST** (локальное→UTC через переход, полночь не «плывёт»); travel-tz override на диапазоне дат; `date_override` (окно и «весь день заблокирован»); группировка UTC-слотов по локальной дате визитёра (в т.ч. слот на границе суток попадает в правильный локальный день). Табличные, фиксированный `now`.
- **Integration (Postgres):** `SlotsReadAdapter` грузит засиженный `event_type` + хосты + расписания без N+1; end-to-end `GET /api/v1/slots` на сценарии (2 хоста с разными часами → union; `date_override`; `travel_schedule` tz; `min_booking_notice` отсекает ранние слоты; пустой `BusyTimesSource`) даёт ожидаемые корзины; `404` (нет event_type), `422` (плохая tz, `end<=start`, окно > 62 дней).

**Рантайм-ошибки:** `ApiError`-стиль сервиса — `404`/`422`. Расчёт read-only, транзакции не мутируют.

## 6. Открытые вопросы к следующим срезам (не блокируют срез 2)

1. **`BusyTimesSource` реализация** (срез 3): поверх таблицы `booking`; политика буферов вокруг занятых интервалов активируется здесь.
2. **Лимиты броней** (срез 3): подсчёт существующих броней per-period; отсечение слотов при достижении лимита.
3. **Кэш результата** (при росте RPS): cal.com-style короткий TTL; инвалидация при новой брони (срез 3).
4. **`getLuckyUser` / выбор конкретного хоста** — на этапе слотов не нужен (union), нужен на write (срез 3): какой хост назначается при бронировании round-robin.
5. **Booker UI** (срез 4) — потребитель этого контракта; `slots`-корзины уже под DatePicker.

## 7. Определение готовности среза 2

- Пакет `slots/` реализован; `GET /api/v1/slots` работает, под `require_api_key`, включён в `main.py` и тест-фикстуру.
- Ядро (`domain.py`/`timezones.py`) — чистое, целочисленное, покрыто unit-тестами (union, нарезка, DST, travel, date_override, группировка).
- `SlotsReadAdapter` — batch-загрузка без N+1; integration-тест end-to-end сценариев + `404`/`422`.
- `BusyTimesSource` подключён как `StubBusyTimesSource` (пусто); буферы/лимиты — проплюблены, инертны.
- `now` инжектируется; тесты детерминированы.
- Полный прогон `pytest` зелёный, Ruff clean; `event-scheduling/CLAUDE.md` + `docs/` обновлены (новый эндпоинт, модуль `slots/`); корневой `docs/architecture/ARCHITECTURE.md` — движок слотов в топологии.
