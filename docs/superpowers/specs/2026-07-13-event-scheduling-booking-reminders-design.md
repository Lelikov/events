# event-scheduling — напоминания для scheduling-броней (срез 4a.3)

> Дизайн-спек. Дата: 2026-07-13.
> `event-scheduling` начинает сам рассылать напоминания за ~1ч до старта для своих броней. Аддитивно — cal.com-путь напоминаний в `event-booking` не трогаем.

## 0. Контекст и рамка

**Проблема (из разведки):** напоминания сегодня — это поллер по БД cal.com в `event-booking` (`ReminderScheduler`): каждые 300с выбирает `Booking` со `startTime ∈ [now+55м, now+65м]`, `status='accepted'`, без маркера `reminder_sent` в `metadata`; публикует `notification.send_requested` (trigger `BOOKING_REMINDER`) + `booking.reminder_sent`; пишет маркер обратно в cal.com `metadata`. Брони `event-scheduling` лежат в своей БД — этот поллер их **не видит**, поэтому scheduling-брони не получают напоминаний (в срезе 4a.2 сознательно отложено сюда).

**Границы среза 4a.3 — ОДНО напоминание за ~1ч до старта** для confirmed scheduling-броней (паритет с cal.com; множественные оффсеты — будущий срез). cal.com-путь (`event-booking/scheduler.py`), event-receiver, event-notifier, event-saver — **не трогаем**.

**Подход A (утверждён):** `event-scheduling` **владеет собственным поллером напоминаний** — данные и состояние «напомнено ли» живут в одном сервисе; переиспользуется уже существующая машинерия (background-loop `run_dispatcher_loop`, `users_client`, `receiver_client`, lifespan). Согласуется с фазовой заменой: event-scheduling становится самодостаточным, а cal.com-поллер в event-booking со временем удаляется.

### Ключевые факты (разведка, сверено с кодом)
- **Маршрутизация event-receiver — только по `(source, type)`** (`event_schemas/queues.py::ROUTING_RULES`, `fnmatch`). `notification.send_requested` → `source_pattern="*"` (любой источник) → `events.notification.commands` → event-notifier. `booking.reminder_sent` → `source="booking", type="booking.reminder_sent"` → `events.booking.lifecycle` → event-saver.
- **Единый ingress-эндпоинт `/event/booking`** принимает CloudEvent'ы ЛЮБОГО `ce-type` и роутит по `(source,type)`. event-booking уже шлёт и `booking.reminder_sent`, и `notification.send_requested` через один и тот же `EVENTS_ENDPOINT_URL=.../event/booking`. Значит `event-scheduling` может слать оба типа через существующий `ReceiverClient.publish(ce_headers, body)` → `/event/booking`, **без нового эндпоинта event-receiver**.
- **Продюсер шлёт «плоский» domain-body** (не `{original, normalized}`) — конвертацию в envelope делает event-receiver на ingress. Мирроринг `publishing/payload.py`.
- **`ce-source="booking"`** — конвенция, которую уже использует outbox-диспетчер event-scheduling (`payload.py` ставит `ce-source: booking`). Подходит обоим типам напоминания (см. правила выше).
- **`booking`-таблица event-scheduling:** `status ∈ {confirmed, cancelled}`, `start_time TIMESTAMPTZ`, `host_user_id`/`client_user_id`, `attendee_time_zone`; индекс `ix_booking_host(host_user_id, status, start_time)`. **Состояния «напомнено» нет.**
- **Reschedule** = единственный UPDATE, меняющий `start_time` (`write_adapter.py`: `UPDATE booking SET start_time=:s, end_time=:e, updated_at=now() ...`). Create — свежий INSERT. Cancel — `status='cancelled'`.
- **`users_client.by_ids([...])`** резолвит `email/name/time_zone/locale` (расширен в 4a.2). `receiver_client` POST'ит `/event/booking` с raw-api-key.

## 1. Архитектура

Новый модуль `event_scheduling/reminders/` (рядом с `publishing/`), зеркалит outbox-паттерн:

```
main.py lifespan
  ├── task: run_dispatcher_loop(...)          # существующий (outbox → booking.lifecycle)
  └── task: run_reminder_loop(...)            # НОВЫЙ (напоминания)
        every REMINDER_INTERVAL:
          remind_once(sql, users, receiver, clock, window, batch):
            due = reminder_read.due_bookings(now, window)     # confirmed ∧ окно ∧ reminder_sent_at IS NULL
            for b in due:
              host, client = users.by_ids([b.host_user_id, b.client_user_id])
              headers_n, body_n = build_reminder_command(b, host, client, now)   # notification.send_requested
              receiver.publish(headers_n, body_n)  → /event/booking → events.notification.commands
              headers_s, body_s = build_reminder_sent(b, client, now)            # booking.reminder_sent
              receiver.publish(headers_s, body_s)  → /event/booking → events.booking.lifecycle
              reminder_write.mark_sent(b.id, now)  # SET reminder_sent_at=now WHERE id AND reminder_sent_at IS NULL
```

**Компоненты (каждый — одна ответственность):**
- `reminders/interfaces.py` — `IReminderReadAdapter.due_bookings(now, shift_from_m, shift_to_m) -> list[DueBookingDTO]`, `IReminderWriteAdapter.mark_sent(booking_id, now) -> None`. (`IUsersClient`/`IReceiverClient` переиспользуются из `publishing/`.)
- `reminders/dto.py` — `DueBookingDTO(id, event_type_id, host_user_id, client_user_id, start_time, end_time, attendee_time_zone, title)` (frozen).
- `reminders/read_adapter.py` — SQL `due_bookings`: `status='confirmed' AND reminder_sent_at IS NULL AND start_time >= now+shift_from AND start_time <= now+shift_to`, JOIN `event_type` для `title`. `ORDER BY start_time`, `LIMIT :batch`.
- `reminders/write_adapter.py` — `mark_sent`: `UPDATE booking SET reminder_sent_at=:now WHERE id=:id AND reminder_sent_at IS NULL`.
- `reminders/payload.py` — `build_reminder_command(due, host, client, now)` и `build_reminder_sent(due, client, now)` → `(ce_headers, body)`.
- `reminders/dispatcher.py` — `remind_once(...)` + `run_reminder_loop(..., stop: asyncio.Event)` (interruptible sleep, per-tick session, log-and-continue — как `run_dispatcher_loop`).
- `ioc.py` — провайдеры новых адаптеров; `config.py` — новые настройки; `main.py` — второй фоновый task.

## 2. Схема БД: миграция `0004_booking_reminder_sent`

Аддитивно (event-scheduling владеет своей БД):
```sql
ALTER TABLE booking ADD COLUMN reminder_sent_at TIMESTAMPTZ NULL;
CREATE INDEX ix_booking_reminder ON booking (start_time)
    WHERE status = 'confirmed' AND reminder_sent_at IS NULL;   -- partial: узкий рабочий набор поллера
```
- `reminder_sent_at IS NULL` = «ещё не напомнено». Дефолт NULL → все существующие/новые брони изначально «не напомнены».
- Партиал-индекс держит горячий набор поллера маленьким (только неотправленные confirmed).
- **DATA_MODEL.md:** `booking` теперь несёт `reminder_sent_at` (nullable).

## 3. Reschedule сбрасывает маркер

В `write_adapter.py::reschedule` добавить `reminder_sent_at=NULL` в UPDATE:
```sql
UPDATE booking SET start_time=:s, end_time=:e, reminder_sent_at=NULL, updated_at=now() WHERE id=:id RETURNING <cols>
```
Перенос на новое время → напоминание перевыстрелит для нового `start_time`. Cancel (`status='cancelled'`) поллер естественно отфильтровывает; create — свежий INSERT с `reminder_sent_at=NULL`.

## 4. Полезные нагрузки (CloudEvents)

Плоские domain-body (envelope строит event-receiver). Оба через `ReceiverClient.publish` → `/event/booking`.

**`notification.send_requested`** (паритет с `event-booking/adapters/events.py::send_notification_command`):
```
headers: {ce-specversion:1.0, ce-id:<detерм.>, ce-source:booking, ce-type:notification.send_requested, ce-time:<iso>}
body: {
  booking_uid: str(booking.id), booking_id: str(booking.id),
  trigger_event: "BOOKING_REMINDER",
  recipients: [ {email:host.email, role:"organizer", locale:host.locale},
                {email:client.email, role:"client", locale:client.locale} ],
  template_data: { booking_uid, start_time:iso, end_time:iso, title,
                   organizer_name, organizer_email, client_name, client_email }
}
```
**`booking.reminder_sent`** (паритет с cal.com-путём — `{email}`; проекция в event-saver):
```
headers: {..., ce-type:booking.reminder_sent, ce-source:booking}
body: { booking_uid: str(booking.id), email: client.email }
```
- **ce-id детерминирован** от dedupe-ключа (`reminder:{id}` / `reminder_sent:{id}`) — стабильный UUID (uuid5), чтобы ретрай не плодил доставку (event-receiver + notifier дедуп по idempotency).
- `recipients`/`role`/`locale` и `template_data` — та же форма, что шлёт event-booking, чтобы event-notifier не различал источник.

## 5. Поток данных

confirmed scheduling-бронь → идёт время → reminder-tick → `due_bookings` (окно + не напомнено) → резолв host+client (`users_client.by_ids`) → publish `notification.send_requested` (→ notifier) → publish `booking.reminder_sent` (→ saver-проекция) → `mark_sent` (`reminder_sent_at=now`). Следующий tick ту же бронь уже не выберет (маркер + партиал-индекс).

## 6. Идемпотентность и порядок (три слоя, как в cal.com)
1. **Выборка** исключает `reminder_sent_at IS NOT NULL`.
2. **`mark_sent`** ставит маркер сразу после отправки; guard `AND reminder_sent_at IS NULL` в UPDATE — гонка двух тиков не задвоит.
3. **Детерминированный ce-id/dedupe-key** — даже краш между publish и mark не задвоит доставленное (event-receiver idempotency-cache + notifier дедуп).
- **Порядок publish:** сначала `notification.send_requested`, затем `booking.reminder_sent`, затем `mark_sent`. Если `notification` прошёл, а `reminder_sent`/`mark` упали — следующий tick повторит оба publish; дубликаты гасятся детерминированным ce-id. Приоритет — не потерять напоминание (лучше повтор, гашёный дедупом, чем тишина).

## 7. Обработка ошибок
- `users_client`/`receiver_client` бросают (сеть/5xx) → `remind_once` логирует и НЕ ставит маркер → бронь попадёт в следующий tick (ретрай). `run_reminder_loop` переживает падение тика (log-and-continue), как `run_dispatcher_loop`.
- Если участник не резолвится (нет email) → пропускаем эту бронь с логом (маркер НЕ ставим — повтор позже), не роняя весь батч. (Аналог деградации в 4a.2, но здесь без частичной доставки: без обоих email напоминание бессмысленно.)
- Битая бронь в батче не должна блокировать остальные: обработка каждой брони изолирована (try/except вокруг одной итерации; сбой → лог + продолжаем).

## 8. Config (event-scheduling `Settings`)
- `reminder_enabled: bool = True` — рубильник (можно отключить поллер, не трогая код).
- `reminder_interval_seconds: float = 60.0` — период тика (окно 10 мин → бронь видна многократно).
- `reminder_shift_from_minutes: int = 55`, `reminder_shift_to_minutes: int = 65` — окно (паритет cal.com).
- `reminder_batch_size: int = 100` — лимит выборки за тик.
- compose/env: дефолтов достаточно; переопределяемы через `.env`.

## 9. Тестирование
- **Миграция 0004** (integration, Docker PG): апгрейд создаёт колонку + партиал-индекс; даунгрейд чист.
- **`due_bookings`** (integration): выбирает только confirmed ∧ окно ∧ `reminder_sent_at IS NULL`; исключает cancelled, вне окна, уже напомненные; уважает `LIMIT`.
- **`mark_sent`** (integration): ставит `reminder_sent_at`; повторный вызов при не-NULL — 0 строк (guard).
- **reschedule сбрасывает маркер** (integration): напомненную бронь reschedule'нуть → `reminder_sent_at IS NULL` → снова due.
- **`build_reminder_command` / `build_reminder_sent`** (unit): ce-headers (type/source/детерм. id), тело (recipients с locale, template_data, `{email}`); стабильность ce-id от ключа.
- **`remind_once`** (unit, fakes для read/users/receiver/write): due → 2 publish в правильном порядке + `mark_sent`; участник без email → skip без mark; сбой receiver → нет mark (ретрай); пустой due → 0 publish.
- **`run_reminder_loop`** (unit): переживает падающий тик; `stop` прерывает сон.
- Полный `pytest` event-scheduling зелёный (Docker PG); ruff clean. cal.com-путь и outbox не затронуты.

## 10. Отложено / вне рамок
1. **Множественные оффсеты (24ч+1ч и т.п.)** — потребуют «какой оффсет отправлен» (набор/битовая маска вместо одного timestamp). Отдельный срез.
2. **Единый общий билдер команды напоминания** между event-booking и event-scheduling — форма дублируется (небольшой дубль). Если понадобится DRY — вынести в `event-schemas` позже; сейчас YAGNI.
3. **Отказ от cal.com-поллера** — когда cal.com уйдёт, `event-booking/scheduler.py` удаляется; этот срез его НЕ трогает.
4. **Напоминание волонтёру vs клиенту раздельно, каналы, тихие часы** — вне рамок (политика notifier/шаблонов).

## 11. Определение готовности среза 4a.3
- Миграция 0004 (колонка + партиал-индекс), reschedule сбрасывает маркер.
- Модуль `reminders/` (read/write/payload/dispatcher/interfaces/dto) + DI + config + второй фоновый task в lifespan.
- Confirmed scheduling-бронь за ~1ч до старта → `notification.send_requested` (→ notifier) + `booking.reminder_sent` (→ saver) опубликованы ровно один раз; маркер поставлен; reschedule перевыставляет.
- Три слоя идемпотентности; поллер переживает сбои тика; участник без email не роняет батч.
- Полный `pytest` + ruff clean; cal.com-напоминания, outbox и 4a.2-провижининг не сломаны.
- Доки: `event-scheduling/CLAUDE.md` (модуль reminders + рубильник), `docs/DATA_MODEL.md` (`reminder_sent_at`), `docs/architecture/MESSAGE_CONTRACTS.md` (event-scheduling — доп. продюсер `notification.send_requested`+`booking.reminder_sent` для своих броней).
