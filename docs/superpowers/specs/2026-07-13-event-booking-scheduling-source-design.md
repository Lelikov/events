# event-booking — reagirovanie na broni event-scheduling (срез 4a.2)

> Дизайн-спек. Дата: 2026-07-13.
> `event-booking` начинает создавать чат/Jitsi/уведомления для броней **новой системы** (`event-scheduling`), которых нет в БД cal.com. Аддитивно — cal.com-путь не трогаем.

## 0. Контекст и рамка

**Проблема (из разведки):** каждый хендлер `event-booking` начинается с `booking = get_booking(booking_uid)` из **БД cal.com** и no-op'ит на `None`. Брони `event-scheduling` лежат в своей БД (не в cal.com) → сейчас чат/Jitsi/напоминания для них не срабатывают, хотя `booking.lifecycle` CloudEvents уже публикуются (срез 4a) и `event-saver` их проецирует.

**Границы среза 4a.2 — чат + Jitsi + уведомления** на `booking.created`/`booking.rescheduled`/`booking.cancelled` для scheduling-броней. **Напоминания отложены в срез 4a.3** (см. §7). cal.com-путь, event-receiver, event-saver, шедулер напоминаний, notifier — **не трогаем**.

**Что меняем:** `event-scheduling` (новый read-эндпоинт), `event-booking` (composite data-source + мелкая правка контроллера + DI).

### Ключевые факты (разведка event-booking)
- Chat/Jitsi/notifications/EventPublisher — **source-agnostic**: работают на `BookingDTO` через Protocol `IBookingDatabaseAdapter` (`interfaces/db.py`). Контроллер зависит от Protocol, не от конкретного класса.
- `BookingDTO` (`dtos.py`) нужны: `uid`, `start_time`, `end_time`, `title`, `user{name,email,time_zone,locale}`, `client{name,email,time_zone,locale}`, `id` (только в constraints/reject-пути). Наш CloudEvent несёт email/tz/uid/времена — **нет имён, title, locale, int id**.
- **cal.com-записи** (`update_booking_video_url`, `mark_reminder_sent`, `reject_booking`) — для scheduling-uid UPDATE'ят 0 строк (естественный no-op); `reject_booking` требует int id.
- **Напоминания = поллер по cal.com** (`scheduler.py`), пишет маркер в cal.com `metadata` — для scheduling-броней ни строки, ни таргета. Отдельная подсистема → срез 4a.3.
- DI: `BookingController` зависит от `IBookingDatabaseAdapter`, но Dishka биндит **конкретный** `BookingDatabaseAdapter` (`ioc.py`), и 2 прямых `.get(BookingDatabaseAdapter)` — в `consumer.py` и `scheduler.py`.

## 1. Архитектура

**Composite data-source за существующим Protocol'ом `IBookingDatabaseAdapter`:**

```
consumer → BookingController → IBookingDatabaseAdapter
                                   └── CompositeBookingDatabaseAdapter
                                         ├── CalcomBookingDatabaseAdapter (существующий, читает cal.com DB)
                                         └── SchedulingBookingSource (НОВЫЙ: HTTP → event-scheduling /bookings/{uid}/detail)
```

- `get_booking(uid)`: пробует cal.com; на `None` — `SchedulingBookingSource`; на `None` от обоих — `None` (хендлер no-op, как сегодня). DTO из scheduling-источника помечен `source="scheduling"`.
- Write-методы (`update_booking_video_url`, `mark_reminder_sent`, `reject_booking`) и `get_attendee_bookings_by_email`/`get_bookings` — **делегируются cal.com-адаптеру** (для scheduling-uid естественно 0-row no-op; constraints/reminders в 4a.2 остаются cal.com-скоуп).
- **Контроллер:** единственная правка — `handle_created` **пропускает** blacklist/constraints/reject-подфлоу, если `booking.source == "scheduling"` (event-scheduling уже подтвердил бронь; rejection ломала бы её). Chat/Jitsi/notifications идут для всех источников.
- Chat/Jitsi/meeting/shortener/notifications/EventPublisher — **без изменений**.

## 2. event-scheduling: новый эндпоинт `GET /api/v1/bookings/{id}/detail`

Под существующим `require_api_key`. Возвращает всё, что нужно `event-booking`, обогащая участников из event-users (у event-scheduling уже есть `users_client` из среза 4a — переиспользуем, он резолвит email/name/time_zone/locale по by-ids):
```json
{
  "uid": "<booking.id>",
  "title": "<event_type.title>",
  "start_time": "…Z", "end_time": "…Z",
  "status": "confirmed|cancelled",
  "host":   {"email": "...", "name": "...", "time_zone": "...", "locale": "..."},
  "client": {"email": "...", "name": "...", "time_zone": "...", "locale": "..."}
}
```
- `uid` = `booking.id` (строка). `title` = из `event_type` брони. `host`/`client` — резолв `host_user_id`/`client_user_id` через event-users by-ids (name/email/locale; `time_zone` client = `booking.attendee_time_zone`, host — из резолва).
- `404` если брони нет. Если event-users не отдаёт участника — деградация: `name`/`locale` = `None`, `email` из резолва (или, если email не резолвится, — ошибка → `event-booking` получит 5xx → сообщение ретраится/DLQ).
- Новый модуль `event_scheduling/booking/detail_*` или расширение `read_adapter`/`service`; `users_client` вынести из `publishing/` в общий доступ (или переиспользовать через DI). Реализуется в `event-scheduling` (наш сервис).

## 3. event-booking: SchedulingBookingSource + Composite

- **`SchedulingBookingSource`** (`adapters/scheduling_source.py`): HTTP-клиент к `event-scheduling` (`GET {EVENT_SCHEDULING_URL}/api/v1/bookings/{uid}/detail`, auth `Authorization: Bearer <SCHEDULING_API_KEY>` — под тем же `require_api_key`, что и остальные эндпоинты event-scheduling). `get(uid) -> BookingDTO | None` (`404` → `None`; иная ошибка → raise → ретрай/DLQ). Маппит JSON → `BookingDTO` c `source="scheduling"`, `id=0` (sentinel, не используется в scheduling-пути), `user`/`client` из host/client, name-fallback = email при `name=None`.
- **`CompositeBookingDatabaseAdapter`** (`adapters/composite.py`) реализует `IBookingDatabaseAdapter`: `get_booking` = cal.com → scheduling-fallback; остальные методы делегируют cal.com-адаптеру.
- **`BookingDTO`** (`dtos.py`): добавить поле `source: str = "calcom"` (дефолт для cal.com-пути; scheduling-источник ставит `"scheduling"`).
- **DI (`ioc.py`)**: биндить `IBookingDatabaseAdapter` на `CompositeBookingDatabaseAdapter(calcom_adapter, scheduling_source)`; обновить прямой `.get(...)` в `consumer.py` на Protocol/композит. `scheduler.py` **оставить** на cal.com-адаптере (напоминания = cal.com-скоуп в 4a.2).
- **Config**: `EVENT_SCHEDULING_URL` (внутренний, порт 8888), `SCHEDULING_API_KEY` (= ключ, который event-scheduling сравнивает в `require_api_key`).

## 4. Поток данных (booking.created от event-scheduling)

`event-scheduling` бронь → CloudEvent `booking.created` (срез 4a) → RabbitMQ → `event-booking` consumer → `handle_created(uid)` → `composite.get_booking(uid)`: cal.com промах → `SchedulingBookingSource.get(uid)` → `GET /bookings/{uid}/detail` → `BookingDTO(source="scheduling")` → (blacklist/reject **пропущены**) → чат создан + Jitsi-URL'ы + уведомления → follow-up CloudEvents (`chat.created`, `meeting.url_created`, `notification.send_requested`) через `POST /event/booking`. Reschedule/cancel — аналогично (reschedule без `previous_booking_uid`: тот же uid, миграция короткой ссылки — no-op).

## 5. Обработка ошибок
- Scheduling-источник недоступен / 5xx → `get` бросает → сообщение ретраится, потом DLQ (как сегодня при сбое cal.com-чтения).
- `404` от detail (брони нет ни в cal.com, ни в scheduling) → `None` → хендлер no-op (как сегодня).
- event-scheduling detail: если event-users не резолвит участника с email → 5xx (лучше явный ретрай, чем частичный чат без client email — чат требует оба email).

## 6. Тестирование
- **event-scheduling `/bookings/{uid}/detail`** (integration): засиженная бронь + fake users-источник → JSON с title/host/client (name/email/tz/locale); `404` на несуществующую; деградация при отсутствии участника.
- **event-booking `SchedulingBookingSource`** (unit, `httpx.MockTransport`): `200` → `BookingDTO(source="scheduling")` (маппинг, name-fallback=email); `404` → `None`; 5xx → raise.
- **`CompositeBookingDatabaseAdapter`** (unit): cal.com hit → cal.com DTO (`source="calcom"`); cal.com miss → scheduling DTO; оба miss → `None`; write-методы делегируют cal.com.
- **Контроллер** (unit): `handle_created` со `source="scheduling"` пропускает blacklist/constraints/reject, но вызывает chat/Jitsi/notifications; со `source="calcom"` — прежнее поведение (rejection-путь цел).
- **e2e** (event-booking): `booking.created` для scheduling-uid (stub event-scheduling detail) → опубликованы `chat.created`/`meeting.url_created`/`notification.send_requested` follow-up'ы (через stub event-receiver).
- Полный `pytest` обоих сервисов зелёный; ruff clean.

## 7. Отложено / открытые вопросы
1. **Напоминания (срез 4a.3):** поллер `event-booking` читает только cal.com и пишет маркер в cal.com metadata. Для scheduling-броней нужен параллельный путь (поллер по `event-scheduling.booking` или event-driven scheduler) + своё «reminder-sent»-хранилище. Отдельный спек.
2. **Хранение client meeting URL:** для scheduling-броней `update_booking_video_url` no-op'ит (нет cal.com-строки). URL всё равно доходит до клиента через `meeting.url_created` + уведомление. Персист в event-scheduling — если понадобится, позже.
3. **`by-ids` возвращает `name`?** Проверить контракт event-users при реализации (для detail-эндпоинта нужны имена); если by-ids не отдаёт name — расширить ответ event-users или отдельный резолв. (В срезе 4a UsersClient парсил email/time_zone — добавить name.)
4. **Blacklist для scheduling-броней:** в 4a.2 пропускаем (event-scheduling — источник подтверждения). Если политика «блэклист отменяет и подтверждённую бронь» понадобится — отдельное решение (кто отменяет: event-booking публикует, event-scheduling исполняет cancel).

## 8. Определение готовности среза 4a.2
- event-scheduling: `GET /api/v1/bookings/{id}/detail` реализован, обогащает из event-users, покрыт тестами; `by-ids` даёт name (или добавлено).
- event-booking: `SchedulingBookingSource` + `CompositeBookingDatabaseAdapter` + `BookingDTO.source` + DI-бинд Protocol'а + контроллер пропускает reject для scheduling; тесты §6 зелёные.
- e2e: scheduling `booking.created` → чат/Jitsi/уведомления follow-up'ы опубликованы (stub-контур).
- Config обоих сервисов + docker-compose env; доки (`event-booking/CLAUDE.md`, `event-scheduling` detail-эндпоинт, `docs/architecture/MESSAGE_CONTRACTS.md`) — chat/Jitsi/notifications теперь работают для scheduling-броней; напоминания — 4a.3.
- Полный `pytest` обоих сервисов + ruff clean; напоминания и cal.com-путь не сломаны.
