# event-organizer — organizer cabinet BFF (срез 6.1)

> Дизайн-спек. Дата: 2026-07-16.
> Новый BFF `event-organizer`: аутентифицированный (пароль) личный кабинет организатора. Организатор входит по email+password → редактирует СВОИ расписание, типы встреч и подключённые календари. Ownership по построению (id из сессии, не из запроса) — закрывает IDOR-находку среза 5. Аддитивно.

## 0. Контекст и рамка

**Референс (изучен вживую на форке cal.com, `localhost:3000`):** кабинет организатора cal.com даёт **Доступность** (расписание: рабочие часы по дням недели + таймзона + переопределения дат + «по умолчанию»), **Типы событий** (CRUD: заголовок/slug/описание/длительность/местоположение, под-вкладки «какое расписание» и «лимиты» — буферы, мин.уведомление, частота), **Календари** (подключить внешний календарь для проверки конфликтов), + просмотр **Бронирований**. Изучены два аккаунта: заполненный (полный набор) и свежий Test User (те же core-возможности, всё пустое: «создай первое расписание/тип события»). Наш core-subset = **расписание + типы встреч + календари** (+ read-список броней). Команды, workflows, маршрутизация, apps-магазин, вебхуки, аналитика — как в cal.com, но вне рамок.

**Проблема:** логина организатора нет. event-users хранит записи `role ∈ {client, organizer}` без учётных данных (auth там — статик-admin-bearer). event-admin имеет полный auth-стек (JWT+bcrypt+TOTP), но это админы панели (`admin_users`), не организаторы. Все `/api/v1/*` event-scheduling — под статик-ключом и принимают произвольный `owner_user_id`/`host_user_id` (admin-authoritative за шлюзом; IDOR-риск отмечен в срезе 5).

**Решение (утверждено — пароль как event-admin, вариант A):** новый BFF `event-organizer` со своей credential-БД. Организатор логинится → JWT несёт его `user_id` → все вызовы к event-scheduling инъектят этот `user_id` как owner/host **из сессии, не из запроса** → ownership by construction. BFF держит `SCHEDULING_API_KEY` серверно.

**Границы среза 6.1 — только BFF (бэкенд):** пароль-логин + credential-БД + admin-провижининг + ownership-прокси 3 ресурсов (+ read-брони). **Фронтенд SPA — срез 6.2** (`event-organizer-frontend`, логин + дашборд-редакторы). event-scheduling/event-users/остальное — не трогаем (переиспользуем существующие API).

### Ключевые факты (сверено с кодом)
- **Auth-паттерн event-admin** (переиспользуем): `services/password.py::PasswordService` (`bcrypt.hashpw`/`checkpw`), `auth.py::create_access_token(settings, email, role)` (JWT HS256, exp, опц. aud/iss), `TokenPayload`, `POST /auth/login`. TOTP у event-admin есть — в 6.1 НЕ тащим (2FA — отдельно).
- **event-scheduling управляющие API** (Bearer `SCHEDULING_API_KEY`): `GET/PUT /api/v1/schedules/{owner_user_id}` (+ `/travel`), CRUD `/api/v1/event-types` (`UpsertEventTypeRequest{slug,title,scheduling_type,duration_minutes,slot_interval_minutes,min_booking_notice_minutes,buffer_before/after_minutes,hosts:[{user_id,schedule_id}],booking_limits}`), `/api/v1/calendars` (create/list?host_user_id/delete/sync), `GET /api/v1/bookings?host_user_id=`.
- **event-users** (Bearer `EVENT_USERS_TOKEN`): `GET /api/users/by-identity?email=&role=organizer` (проверка при провижининге).
- Монорепо-конвенции: Python 3.14, uv, FastAPI, Dishka, SqlExecutor (raw `:param`), alembic, Ruff 120, frozen DTO, **без elif/избегать else**. Порт: host **8006**, internal 8888. Своя БД `event_organizer`. Трекается КОРНЕВЫМ репо.

## 1. Архитектура

```
organizer browser (event-organizer-frontend, 6.2)
        │  JWT (Authorization: Bearer <session token>)
        ▼
  event-organizer BFF  (own DB event_organizer; holds SCHEDULING_API_KEY + EVENT_USERS_TOKEN)
    auth: POST /auth/login (email+password → bcrypt → JWT{sub=user_id})
    /api/me/*  (require_organizer → OrganizerIdentity{user_id})  — id injected from session:
        ├── schedule    → event-scheduling /api/v1/schedules/{me.user_id}
        ├── event-types → /api/v1/event-types (filter/verify by host==me.user_id)
        ├── calendars   → /api/v1/calendars (inject host_user_id=me; fetch-verify on delete/sync)
        └── bookings    → GET /api/v1/bookings?host_user_id=me (read)
    admin: POST /admin/organizers (static ORGANIZER_ADMIN_KEY) — seed credentials
        SchedulingClient (Bearer SCHEDULING_API_KEY), UsersClient (Bearer EVENT_USERS_TOKEN)
```

**Модули (`event_organizer/`):**
- `config.py` — Settings (upstream URLs+keys, postgres_dsn, jwt_secret, jwt_expire_minutes, organizer_admin_key).
- `errors.py` — DomainError + Unauthorized/Forbidden/NotFound/Validation/Upstream/Conflict.
- `auth/password.py` — `PasswordService` (bcrypt; копия event-admin).
- `auth/jwt.py` — `create_access_token(user_id, email)`, `decode_token(token) -> OrganizerIdentity`.
- `auth/identity.py` — `OrganizerIdentity(user_id: UUID, email: str)` frozen; `require_organizer` Dishka/FastAPI dependency (читает `Authorization: Bearer`, декодит JWT).
- `credentials/` — `dto.py` (OrganizerCredentialDTO), `interfaces.py` (ICredentialAdapter), `read_write_adapter.py` (get_by_email, create).
- `adapters/scheduling_client.py`, `adapters/users_client.py` — httpx (Bearer).
- `services/` — `login_service.py` (verify → token), `provisioning_service.py` (verify-organizer-in-users → hash → create), `cabinet_service.py` (ownership-логика event-types/calendars).
- `routers/auth.py` (`/auth/login`), `routers/me.py` (`/api/me/*`), `routers/admin.py` (`/admin/organizers`).
- `schemas/` — Pydantic req/resp.
- `ioc.py`, `main.py`, `routes.py` (health/ready/metrics), `telemetry.py`/`metrics.py`/`logger.py` (копии), `alembic/` (migration 0001).

## 2. Схема БД: миграция `0001_organizer_credential`
```sql
CREATE TABLE organizer_credential (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL,          -- event-users organizer UUID (owner/host id)
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,          -- bcrypt
    disabled       BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_organizer_credential_email UNIQUE (email),
    CONSTRAINT uq_organizer_credential_user  UNIQUE (user_id)
);
```
- `email` и `user_id` уникальны (один логин на организатора). `disabled` — мягкая блокировка.

## 3. Аутентификация
- **`PasswordService`** — bcrypt (копия event-admin `services/password.py`).
- **JWT** — HS256, `sub = str(user_id)`, `email`, `exp = now + JWT_EXPIRE_MINUTES` (дефолт 60). `create_access_token` / `decode_token` (валидирует подпись+exp; на ошибке → Unauthorized).
- **`POST /auth/login`** body `{email, password}` → `credential = get_by_email(email)`; если нет / `disabled` / `verify(password, hash)` false → **401** (единое сообщение, без утечки «нет такого email»). Успех → `{access_token, token_type:"bearer"}` (JWT c user_id).
- **`require_organizer`** — читает `Authorization: Bearer <jwt>` → `decode_token` → `OrganizerIdentity(user_id, email)`; нет/битый/просрочен → **401**. Все `/api/me/*` под ним.
- **Rate-limit/login-guard** — event-admin имеет `LoginGuard`; в 6.1 — базовый (или отложить с пометкой). Отмечено §8.

## 4. Admin-провижининг учёток
- **`POST /admin/organizers`** под статик-ключом `ORGANIZER_ADMIN_KEY` (Bearer, как require_api_key). Body `{user_id, email, password}`:
  - Проверить, что это организатор: `GET event-users /api/users/by-identity?email=<email>&role=organizer` → 404 → **422** «not an organizer». (Опц. сверить user_id.)
  - `hash = PasswordService.hash(password)` → `create(user_id, email, hash)`; дубль email/user_id → **409**.
  - → 201 `{id, user_id, email}`.
- Плюс seed-хелпер/скрипт для dev. Self-регистрация и сброс пароля — §8 (отложено).

## 5. Ownership-прокси (`/api/me/*`, id из сессии)
`SchedulingClient` (Bearer SCHEDULING_API_KEY) к event-scheduling. `me = require_organizer`.

- **Расписание** (тривиальный ownership — id = owner):
  - `GET /api/me/schedule` → `GET /api/v1/schedules/{me.user_id}` (bundle).
  - `PUT /api/me/schedule` body `{time_zone, weekly_hours:[{day_of_week,start_time,end_time}], date_overrides:[{date,start_time,end_time}]}` → `PUT /api/v1/schedules/{me.user_id}`.
  - `PUT /api/me/schedule/travel` → `/travel`.
- **Типы встреч** (ownership = я хост):
  - `GET /api/me/event-types` → `GET /api/v1/event-types` → фильтр: оставить те, где `me.user_id ∈ hosts[].user_id`.
  - `POST /api/me/event-types` body `{slug,title,duration_minutes,...,booking_limits}` → BFF резолвит `schedule_id` организатора (из его schedule-бандла — **verify-at-impl:** бандл должен отдавать id расписания; если нет — добавить в scheduling или хранить дефолт) → инъектит `hosts:[{user_id: me.user_id, schedule_id}]` → `POST /api/v1/event-types`. (Требует, чтобы у организатора уже было расписание — как в cal.com «создай расписание первым».)
  - `PUT /api/me/event-types/{id}` / `DELETE` → `GET /api/v1/event-types/{id}` → если `me.user_id ∉ hosts` → **403** (или 404); иначе проксировать (на PUT — сохранить/переустановить себя хостом).
  - Мульти-хост/командные типы — вне рамок (одиночный хост-владелец).
- **Календари** (ownership: инъекция + fetch-verify — прямой IDOR-фикс среза 5):
  - `GET /api/me/calendars` → `GET /api/v1/calendars?host_user_id={me.user_id}`.
  - `POST /api/me/calendars` body `{url}` → `POST /api/v1/calendars {host_user_id: me.user_id, url}`.
  - `DELETE /api/me/calendars/{id}` / `POST /api/me/calendars/{id}/sync` → сперва `GET /api/v1/calendars?host_user_id=me` → если `id` не в списке → **404** (не владелец) → иначе проксировать delete/sync.
- **Брони (read):** `GET /api/me/bookings` → `GET /api/v1/bookings?host_user_id={me.user_id}` — только чтение (как вкладка «Бронирования» в cal.com).

## 6. Обработка ошибок (без утечек)
- 401 — нет/битый/просроченный токен, неверный логин (единое сообщение).
- 403 — попытка тронуть чужой ресурс (event-type/calendar не принадлежит) — либо 404 (не раскрываем существование).
- 404 — нет ресурса. 409 — дубль (провижининг). 422 — валидация ввода / не организатор.
- 502 — upstream (event-scheduling/event-users) сбой/5xx; сырые тела/стеки наружу не проксируем.
- Секреты (SCHEDULING_API_KEY, EVENT_USERS_TOKEN, jwt_secret, пароли/хеши) — только серверно, никогда в ответах/логах.

## 7. Тестирование (pytest; Docker PG для credential-БД; httpx.MockTransport для upstream)
- **PasswordService** (unit): hash≠plain; verify true/false.
- **jwt** (unit): create→decode round-trip (user_id/email); битый/просроченный → Unauthorized.
- **credential adapter** (integration DB): create + get_by_email; дубль email/user_id → Conflict.
- **login** (integration, TestClient): верные creds → JWT; неверный пароль/нет email/disabled → 401 (единое сообщение).
- **require_organizer** (integration): валидный Bearer → 200; нет/битый → 401.
- **provisioning** (integration, stub users-client): organizer → 201; не организатор (users 404) → 422; дубль → 409; без admin-ключа → 401.
- **schedule proxy** (integration, stub scheduling-client): GET/PUT ходят на `/api/v1/schedules/{session.user_id}` — id из токена, не из запроса (тест с двумя сессиями).
- **event-types ownership** (integration): list фильтрует к моим; create инъектит меня хостом; PUT/DELETE чужого (hosts без меня) → 403/404.
- **calendars ownership** (integration): create инъектит host_user_id=me; delete/sync чужого id (нет в моём списке) → 404; своего → проксируется.
- **bookings read** (integration): проксирует с host_user_id=me.
- Полный `pytest` + ruff clean; ничего существующее не сломано.

## 8. Отложено / вне рамок
1. **Фронтенд SPA** — срез 6.2 (`event-organizer-frontend`: логин + дашборд-редакторы, зеркалит event-admin-frontend auth-flow + booker-frontend).
2. **TOTP/2FA, login-guard/rate-limit, сброс пароля, self-регистрация, смена пароля** — event-admin имеет часть; в 6.1 — базовый логин + admin-provisioning. Отдельные hardening-срезы.
3. **Команды, workflows, маршрутизация, apps-магазин, вебхуки, аналитика, out-of-office, мульти-хост/командные типы событий** — как в cal.com, но вне рамок.
4. **Slice-5 SSRF hardening** (iCal fetch) остаётся отдельным pre-prod блокером — не часть 6.1.
5. **schedule_id для create-event-type**: если schedule-бандл не отдаёт id расписания — мелкое расширение scheduling (verify-at-impl, §5).

## 9. Определение готовности 6.1
- Сервис `event-organizer` поднимается (`/health`,`/ready`,`/metrics`); миграция 0001; своя БД.
- Пароль-логин (bcrypt+JWT), admin-провижининг (проверка organizer в event-users, dedup).
- `/api/me/*`: расписание (GET/PUT), типы встреч (list/create/put/delete с ownership), календари (list/create/delete/sync с ownership), брони (read) — **id всегда из сессии**; чужое → 403/404.
- Секреты серверно; ошибки без утечек; тесты §7 зелёные; ruff clean.
- Docker + docker-compose (host 8006, deps event-scheduling+event-users+postgres) + `event-organizer/CLAUDE.md` + корневой CLAUDE.md (сервис+порт) + `docs/architecture/ONBOARDING.md`/`ARCHITECTURE.md` (новый BFF, organizer-auth, ownership by construction — закрывает slice-5 IDOR).
