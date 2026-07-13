# event-booker — public booking BFF (срез 4b.1)

> Дизайн-спек. Дата: 2026-07-14.
> Новый stateless FastAPI-сервис `event-booker`: публичный (без авторизации) контур для гостевого бронирования. Держит серверные ключи к event-scheduling и event-users, превращает гостя (имя+email) в `client_user_id` и создаёт бронь. Аддитивно — существующие сервисы не меняем.

## 0. Контекст и рамка

**Проблема:** у event-scheduling НЕТ публичного контура — все `/api/v1/*` под статичным admin-bearer (`require_api_key`), а `POST /bookings` требует готовый `client_user_id` (UUID из event-users). event-users `/api/users/*` тоже под admin-bearer. Публичная страница бронирования (как cal.com Booker) не может слать эти ключи в браузер и не имеет гостевого «имя+email → бронь».

**Решение (утверждено, вариант A):** отдельный BFF-сервис `event-booker` — единственная точка доверия для публики. Держит `SCHEDULING_API_KEY` + `EVENT_USERS_TOKEN` серверно; отдаёт наружу ТОЛЬКО безопасные booker-операции; резолвит гостя через event-users (`by-identity → create`); создаёт бронь в event-scheduling.

**Границы 4b.1 — только BFF (бэкенд):** сервис + публичный API + gost-резолв + HTTP-клиенты + конфиг + тесты + Docker/compose + доки. **Фронтенд SPA — отдельный срез 4b.2** (`event-booker-frontend`, React, потребляет этот BFF). event-scheduling, event-users, event-booking, event-receiver — **не трогаем**.

**Декомпозиция 4b:** 4b.1 = `event-booker` BFF (этот спек) → 4b.2 = `event-booker-frontend` SPA (отдельный спек после мержа 4b.1).

### Ключевые факты (разведка, сверено с кодом)
- **event-scheduling** (Bearer `SCHEDULING_API_KEY`): `GET /api/v1/event-types` → `{items:[{id,slug,title,description?,duration_minutes}]}`; `GET /api/v1/event-types/{id}` → EventTypeResponse; `GET /api/v1/slots?event_type_id=&start=&end=&time_zone=` → `{event_type_id, time_zone, slots: {"<date>": ["<iso-time>", ...]}}`; `POST /api/v1/bookings` body `{event_type_id, client_user_id, start_time, attendee_time_zone}` (+ заголовки `actor_source`, `actor_user_id`) → 201 `{id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, created_at}`. Слот-конфликт → 409 (exclusion constraint).
- **event-users** (Bearer `EVENT_USERS_TOKEN` — статичный `api_bearer_token`): `GET /api/users/by-identity?email=&role=client` → 200 `{id,email,name,role,time_zone}` или 404; `POST /api/users` body `{email, name?, role:"client"|"organizer", time_zone}` → 201 UserResponse, **409** при дубле (email+role).
- **Монорепо-конвенции (Python):** 3.14, uv, FastAPI, Dishka, Ruff 120, frozen-dataclass DTO, **без elif / избегать else**, `interfaces/` протоколы, httpx-клиенты, structlog, `/health`+`/ready`+`/metrics`, OTel (gated). Мирроринг скелета event-scheduling (`main.py`, `routes.py`, `telemetry.py`, `metrics.py`). **БД нет** — сервис stateless.
- Порты: заняты 8000–8004, 8080, 8888, 3000. `event-booker` → host **8005**, internal 8888.
- **Репо:** `event-booker` трекается КОРНЕВЫМ репо `events` (как event-scheduling).

## 1. Архитектура

```
public browser (event-booker-frontend, 4b.2)
        │  (no auth; nginx same-origin proxy)
        ▼
  event-booker BFF  (holds SCHEDULING_API_KEY + EVENT_USERS_TOKEN)
    routers/public.py  →  GuestBookingService
        ├── SchedulingClient (Bearer SCHEDULING_API_KEY) → event-scheduling
        └── UsersClient       (Bearer EVENT_USERS_TOKEN)  → event-users
```

**Модули (каждый — одна ответственность):**
- `event_booker/config.py` — `Settings` (upstream URLs + два ключа + CORS origins).
- `event_booker/interfaces/clients.py` — `ISchedulingClient`, `IUsersClient` (Protocols).
- `event_booker/adapters/scheduling_client.py` — httpx-клиент к event-scheduling (list/get event-types, slots, create booking).
- `event_booker/adapters/users_client.py` — httpx-клиент к event-users (`get_client_by_email`, `create_client`).
- `event_booker/services/guest_booking.py` — `GuestBookingService.book(...)`: резолв-или-создание клиента → создание брони.
- `event_booker/dto.py` — frozen DTO для входа/выхода сервиса.
- `event_booker/schemas/public.py` — Pydantic request/response для публичного API.
- `event_booker/routers/public.py` — публичные эндпоинты (без auth-зависимости).
- `event_booker/errors.py` — доменные ошибки (`UpstreamError`, `SlotUnavailableError`, `ValidationError`) → HTTP.
- `event_booker/routes.py` — `root_router` (`/health`, `/ready`, `/metrics`).
- `event_booker/ioc.py` — Dishka providers (Settings, два клиента APP-scope, сервис).
- `event_booker/main.py` — FastAPI app, tracing, роутеры (без lifespan-задач — БД/фоновых нет).
- `event_booker/telemetry.py`, `metrics.py` — копии паттерна event-scheduling (OTel gated, HTTP-метрики).

## 2. Публичный API (без авторизации — BFF сам граница доверия)

Все под префиксом `/api/public`. Наружу отдаём ТОЛЬКО booker-safe поля (не протекают `host_user_id`/`client_user_id`).

1. **`GET /api/public/event-types`** → `{items:[{id, slug, title, description?, duration_minutes}]}`. Проксирует scheduling list, проецирует публичные поля.
2. **`GET /api/public/event-types/{id}`** → один тип (для заголовка страницы бронирования). 404 если нет.
3. **`GET /api/public/slots?event_type_id=&start=&end=&time_zone=`** → `{event_type_id, time_zone, slots: {"<date>":["<iso>"]}}`. Проксирует scheduling slots 1:1. Валидация окна/tz — на стороне scheduling (BFF пробрасывает 4xx как 400/422).
4. **`POST /api/public/bookings`** body `{event_type_id: UUID, name: str, email: EmailStr, start_time: datetime, time_zone: str}` → **гостевое бронирование**:
   - a. Резолв клиента: `GET users /api/users/by-identity?email=<email>&role=client`. На **404** → `POST users /api/users {email, name, role:"client", time_zone}`. На **409** от create (гонка/дубль) → повторный `by-identity` (берём существующего). Получаем `client_user_id`.
   - b. `POST scheduling /api/v1/bookings {event_type_id, client_user_id, start_time, attendee_time_zone: time_zone}` c заголовком `actor_source: "booker"`.
   - c. Ответ (публичная проекция) → `201 {booking_id, event_type_title, start_time, end_time, status, time_zone}`. НЕ отдаём внутренние user-id.

> `notes`/комментарий гостя — вне рамок (scheduling не хранит). Мультивыбор типов, лендинг-список организатора, «моя бронь по токену», cancel/reschedule — отложены (см. §7).

## 3. GuestBookingService (ядро)

```
book(event_type_id, name, email, start_time, time_zone) -> BookingConfirmation:
    client_id = users.get_client_by_email(email)            # by-identity, None на 404
    if client_id is None:
        client_id = users.create_client(email, name, time_zone)   # 201 → id; на 409 → get_client_by_email (must exist)
    booking = scheduling.create_booking(event_type_id, client_id, start_time, time_zone)
    et = scheduling.get_event_type(event_type_id)           # для title в подтверждении (или кэш из шага list)
    return BookingConfirmation(booking.id, et.title, booking.start_time, booking.end_time, booking.status, time_zone)
```
- **Резолв-или-создание идемпотентен** относительно гонок: create-409 → refetch (клиент уже есть). Роль всегда `"client"`.
- **Порядок:** резолв клиента → создание брони. Если бронь падает (слот занят) — клиент уже создан (безвредно; тот же email переиспользуется при повторной попытке).
- `get_event_type` для title можно звать до create (для сообщения) — на выбор реализации; главное, что подтверждение несёт человекочитаемый title, не UUID.

## 4. Обработка ошибок (наружу — без утечек внутренностей)
- **Слот занят** (scheduling `POST /bookings` → 409) → `409 {detail:"slot no longer available"}`.
- **Плохой ввод** (невалидный email/tz, start в прошлом, несуществующий event_type) → `422`/`400`. event_type отсутствует (scheduling 404 при create или get) → `404 {detail:"event type not found"}`.
- **Upstream 5xx / сеть** → `502 {detail:"upstream unavailable"}` (лог с деталями внутри, наружу — обобщённо).
- **users create вернул не 201/409** → `502`.
- Никогда не проксируем сырые upstream-тела/стек-трейсы наружу; логируем структурировано (structlog) внутри.

## 5. Безопасность и злоупотребление
- BFF — **единственная граница доверия**: наружу нет admin-операций (нет list всех броней, нет CRUD типов, нет произвольного создания organizer — роль жёстко `"client"`).
- Ключи (`SCHEDULING_API_KEY`, `EVENT_USERS_TOKEN`) только серверно (env/Vault), никогда в ответах/логах.
- CORS: разрешить сконфигурированные origins (`BOOKER_CORS_ORIGINS`, дефолт — пусто/же-origin; в dev — origin фронта). В 4b.2 фронт за тем же nginx same-origin → CORS не нужен, но флаг оставляем.
- **Rate-limiting / captcha — ОТЛОЖЕНО** (§7): публичный `POST /bookings` создаёт пользователей и брони → уязвим к спаму. В 4b.1 — базовая валидация ввода + жёсткая роль `client`; anti-abuse (per-IP лимит, captcha, email-верификация) — отдельный hardening-срез. Явно зафиксировано как риск.

## 6. Тестирование (pytest + httpx.MockTransport — БД нет)
- **SchedulingClient** (unit): list/get event-types, slots (маппинг), create_booking (Bearer-заголовок, тело, 201-парсинг, 409→SlotUnavailableError, 404→NotFound, 5xx→UpstreamError).
- **UsersClient** (unit): `get_client_by_email` (200→id, 404→None), `create_client` (201→id, 409→ConflictError, 5xx→UpstreamError); Bearer-заголовок; `role=client`.
- **GuestBookingService** (unit, fakes): существующий клиент → бронь без create; новый клиент (by-identity 404) → create+бронь; create-409 → refetch→бронь; слот занят → пробрасывает SlotUnavailableError, клиент создан.
- **Публичные роутеры** (integration, FastAPI TestClient + застабленные клиенты через Dishka override): 4 эндпоинта — happy path + коды ошибок (409 слот, 404 тип, 422 ввод, 502 upstream); проверка, что ответы НЕ содержат `client_user_id`/`host_user_id`.
- **health/ready/metrics** доступны без auth; публичные эндпоинты без Bearer возвращают 2xx/4xx (не 401).
- Полный `pytest` зелёный; ruff clean.

## 7. Отложено / вне рамок 4b.1
1. **Фронтенд SPA** — срез 4b.2 (`event-booker-frontend`).
2. **Anti-abuse** (rate-limit, captcha, email-верификация) — отдельный hardening-срез; в 4b.1 зафиксирован риск.
3. **«Моя бронь по токену» / cancel / reschedule гостем** — нужен подписанный booking-token; отдельный срез.
4. **Лендинг-список организатора / мультивыбор типов** — 4b.2/позже (API `GET /event-types` уже готов).
5. **Helm chart + dual-CI** для event-booker — productionization; в 4b.1 включаем Dockerfile + docker-compose (dev-контур), Helm/CI по образцу существующих сервисов — как быстрый follow-up (или последняя задача плана, если тривиально по шаблону).
6. **Notes/комментарий гостя, кастомные поля брони** — scheduling не хранит; вне рамок.

## 8. Определение готовности 4b.1
- Сервис `event-booker` поднимается (`/health`,`/ready`,`/metrics`), 4 публичных эндпоинта работают, ключи серверные.
- Гостевое бронирование сквозь dev-контур: новый гость → создан client в event-users → бронь в event-scheduling → подтверждение без утечки внутренних id. Существующий email — переиспользуется (без дубля).
- Слот-конфликт → 409; несуществующий тип → 404; upstream-сбой → 502; ввод-мусор → 422.
- Тесты §6 зелёные; ruff clean; ничего в существующих сервисах не сломано.
- Docker + docker-compose (host 8005) + `event-booker/CLAUDE.md` + корневой `docs/architecture/ARCHITECTURE.md`/`ONBOARDING.md` (новый сервис) + `docker-compose` порт-таблица в корневом CLAUDE.md.
