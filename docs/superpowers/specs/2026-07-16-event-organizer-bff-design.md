# event-organizer — organizer cabinet BFF (срез 6.1)

> Дизайн-спек. Дата: 2026-07-16.
> Новый BFF `event-organizer`: аутентифицированный (пароль) личный кабинет организатора. Организатор входит по email+password → **настраивает своё расписание, смотрит свои брони, правит настройки ЛК (профиль/таймзона/пароль)**. Ownership по построению (id из сессии, не из запроса) — закрывает IDOR-находку среза 5. Аддитивно.

## 0. Контекст и рамка

**Референс (изучен вживую на форке cal.com, `localhost:3000`) — роли уточнены:**
- **Админ** (напр. «Александр Леликов») — полный кабинет cal.com (типы событий, команды, apps, календари, аналитика). **Его функционал — в admin-frontend, НЕ в ЛК организатора.**
- **Организатор** (Test User) — урезанный кабинет: **Доступность** (расписание), **Бронирования** (просмотр), **Настройки** (Профиль: имя/email/bio/telegram/аватар; Общие: язык/часовой пояс; Внешний вид; Безопасность → Пароль). **Типов событий и Календарей у организатора НЕТ** (проверено на Test User: скрыты/отсутствуют).

**Объём ЛК организатора (утв.):** **расписание + просмотр броней + настройки ЛК** (профиль: имя + таймзона; смена пароля). **Типы событий — НЕ включаем. Календари — НЕ включаем** (у организатора-референса их нет; подключение календарей — админ/позже). bio/аватар/telegram/язык/внешний-вид — фичи cal.com вне нашего домена, вне рамок.

**Проблема:** логина организатора нет. event-users хранит `role ∈ {client, organizer}` без учётных данных (auth там — статик-admin-bearer). event-admin имеет JWT+bcrypt+TOTP, но это админы панели (`admin_users`), не организаторы. Все `/api/v1/*` event-scheduling — под статик-ключом, принимают произвольный `owner_user_id`/`host_user_id` (IDOR-риск отмечен в срезе 5).

**Решение (утв. — пароль как event-admin, вариант A):** новый BFF `event-organizer` со своей credential-БД. Логин → JWT c `user_id` → все вызовы к event-scheduling/event-users инъектят этот `user_id` **из сессии, не из запроса** → ownership by construction. BFF держит `SCHEDULING_API_KEY` + `EVENT_USERS_TOKEN` серверно.

**Границы среза 6.1 — только BFF (бэкенд):** пароль-логин + credential-БД + admin-провижининг + `/api/me/*` (расписание, брони-read, профиль, смена пароля). **Фронтенд SPA — срез 6.2** (`event-organizer-frontend`). event-scheduling/event-users/остальное — не трогаем (переиспользуем API).

### Ключевые факты (сверено с кодом)
- **Auth-паттерн event-admin** (переиспользуем): `services/password.py::PasswordService` (`bcrypt.hashpw`/`checkpw`), `auth.py::create_access_token` (JWT HS256, exp), `TokenPayload`, `POST /auth/login`. TOTP/login-guard — event-admin имеет, в 6.1 НЕ тащим.
- **event-scheduling** (Bearer `SCHEDULING_API_KEY`): `GET/PUT /api/v1/schedules/{owner_user_id}` (+ `/travel`); `GET /api/v1/bookings?host_user_id=`. Расписание-bundle: `{time_zone, weekly_hours:[{day_of_week,start_time,end_time}], date_overrides:[{date,start_time,end_time}], travel_schedules}`.
- **event-users** (Bearer `EVENT_USERS_TOKEN`): `GET /api/users/id/{user_id}` → `{id,email,name,role,time_zone}`; `PATCH /api/users/id/{user_id}` (`UpdateUserRequest{email?,name?,role?,time_zone?}`) — правим ТОЛЬКО `name`+`time_zone`; `GET /api/users/by-identity?email=&role=organizer` (провижининг).
- Монорепо: Python 3.14, uv, FastAPI, Dishka, SqlExecutor (raw `:param`), alembic, Ruff 120, frozen DTO, **без elif/избегать else**. Порт host **8006**, internal 8888. Своя БД `event_organizer`. Трекается КОРНЕВЫМ репо.

## 1. Архитектура

```
organizer browser (event-organizer-frontend, 6.2)
        │  JWT (Authorization: Bearer <session token>)
        ▼
  event-organizer BFF  (own DB event_organizer; holds SCHEDULING_API_KEY + EVENT_USERS_TOKEN)
    auth:  POST /auth/login (email+password → bcrypt → JWT{sub=user_id})
    /api/me/*  (require_organizer → OrganizerIdentity{user_id})  — id ALWAYS from session:
        ├── GET/PUT /api/me/schedule (+ /travel) → event-scheduling /api/v1/schedules/{me.user_id}
        ├── GET     /api/me/bookings              → event-scheduling /api/v1/bookings?host_user_id=me
        ├── GET/PUT /api/me/profile               → event-users /api/users/id/{me.user_id}  (name, time_zone)
        └── PUT     /api/me/password              → own credential store (verify old → hash new)
    admin: POST /admin/organizers (static ORGANIZER_ADMIN_KEY) — seed credentials
        SchedulingClient (Bearer SCHEDULING_API_KEY), UsersClient (Bearer EVENT_USERS_TOKEN)
```

**Модули (`event_organizer/`):**
- `config.py`, `errors.py` (DomainError + Unauthorized/Forbidden/NotFound/Validation/Upstream/Conflict).
- `auth/password.py` (`PasswordService`, bcrypt — копия event-admin), `auth/jwt.py` (`create_access_token(user_id,email)`, `decode_token`), `auth/identity.py` (`OrganizerIdentity{user_id,email}` frozen; `require_organizer` dependency).
- `credentials/` — `dto.py`, `interfaces.py`, `adapter.py` (`get_by_email`, `create`, `update_password_hash`).
- `adapters/scheduling_client.py`, `adapters/users_client.py` (httpx, Bearer).
- `services/` — `login_service.py`, `provisioning_service.py`, `profile_service.py`, `password_service.py` (смена пароля).
- `routers/auth.py` (`/auth/login`), `routers/me.py` (`/api/me/*`), `routers/admin.py` (`/admin/organizers`).
- `schemas/`, `ioc.py`, `main.py`, `routes.py` (health/ready/metrics), `telemetry.py`/`metrics.py`/`logger.py` (копии), `alembic/` (0001).

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

## 3. Аутентификация
- **`PasswordService`** — bcrypt (копия event-admin `services/password.py`).
- **JWT** — HS256, `sub = str(user_id)`, `email`, `exp = now + JWT_EXPIRE_MINUTES` (60). `create_access_token`/`decode_token` (валидирует подпись+exp; ошибка → Unauthorized).
- **`POST /auth/login`** body `{email, password}` → `get_by_email`; нет / `disabled` / `verify` false → **401** (единое сообщение, без утечки «нет email»). Успех → `{access_token, token_type:"bearer"}`.
- **`require_organizer`** — `Authorization: Bearer <jwt>` → `decode_token` → `OrganizerIdentity{user_id,email}`; нет/битый/просрочен → **401**. Все `/api/me/*` под ним.
- Rate-limit/login-guard — базовый или отложить (§8).

## 4. Admin-провижининг учёток
- **`POST /admin/organizers`** под статик-ключом `ORGANIZER_ADMIN_KEY`. Body `{user_id, email, password}`:
  - `GET event-users /api/users/by-identity?email=<email>&role=organizer` → 404 → **422** «not an organizer».
  - `hash = PasswordService.hash(password)` → `create(user_id, email, hash)`; дубль email/user_id → **409** → 201 `{id, user_id, email}`.
- Seed-хелпер для dev. Self-регистрация/сброс пароля — §8.

## 5. `/api/me/*` (id всегда из сессии — ownership by construction)
`me = require_organizer`. `SchedulingClient` (Bearer SCHEDULING_API_KEY), `UsersClient` (Bearer EVENT_USERS_TOKEN).

- **Расписание** (id = owner, из сессии):
  - `GET /api/me/schedule` → `GET /api/v1/schedules/{me.user_id}` (bundle).
  - `PUT /api/me/schedule` body `{time_zone, weekly_hours:[{day_of_week,start_time,end_time}], date_overrides:[{date,start_time,end_time}]}` → `PUT /api/v1/schedules/{me.user_id}`.
  - `PUT /api/me/schedule/travel` → `/travel`.
- **Брони (read):** `GET /api/me/bookings` → `GET /api/v1/bookings?host_user_id={me.user_id}` — только чтение.
- **Профиль:** `GET /api/me/profile` → `GET event-users /api/users/id/{me.user_id}` → проекция `{name, email, time_zone}` (email — read-only). `PUT /api/me/profile {name, time_zone}` → `PATCH event-users /api/users/id/{me.user_id}` c ТОЛЬКО `name`+`time_zone` (никогда email/role).
- **Смена пароля:** `PUT /api/me/password {old_password, new_password}` → взять свою credential → `verify(old, hash)` false → **401/422**; иначе `hash(new)` → `update_password_hash`. (Никаких id из запроса — только своя сессия.)

> Никаких fetch-verify: у всех эндпоинтов id ресурса = `me.user_id` из токена. IDOR-класс невозможен by construction.

## 6. Обработка ошибок (без утечек)
- 401 — нет/битый/просроченный токен; неверный логин; неверный старый пароль (единое сообщение). 404 — нет ресурса. 409 — дубль (провижининг). 422 — валидация / не организатор. 502 — upstream 5xx/сбой (сырые тела/стеки наружу не проксируем).
- Секреты (SCHEDULING_API_KEY, EVENT_USERS_TOKEN, jwt_secret, пароли/хеши) — только серверно, никогда в ответах/логах. Профиль-проекция не отдаёт role/internal-id.

## 7. Тестирование (pytest; Docker PG для credential-БД; httpx.MockTransport для upstream)
- **PasswordService** (unit): hash≠plain; verify true/false.
- **jwt** (unit): create→decode (user_id/email); битый/просроченный → Unauthorized.
- **credential adapter** (integration DB): create + get_by_email + update_password_hash; дубль email/user_id → Conflict.
- **login** (integration): верные creds → JWT; неверный пароль/нет email/disabled → 401 (единое сообщение).
- **require_organizer** (integration): валидный Bearer → 200; нет/битый → 401.
- **provisioning** (integration, stub users): organizer → 201; не организатор (users 404) → 422; дубль → 409; без admin-ключа → 401.
- **schedule proxy** (integration, stub scheduling): GET/PUT ходят на `/api/v1/schedules/{session.user_id}` — id из токена, не из запроса (две разные сессии → разные owner).
- **bookings read** (integration): проксирует с `host_user_id=me`.
- **profile** (integration, stub users): GET проецирует name/email/time_zone; PUT шлёт ТОЛЬКО name+time_zone на `/api/users/id/{me.user_id}` (не email/role).
- **change password** (integration): верный old → хеш обновлён + старый больше не логинит, новый логинит; неверный old → 401/422.
- Полный `pytest` + ruff clean; ничего существующее не сломано.

## 8. Отложено / вне рамок
1. **Фронтенд SPA** — срез 6.2 (`event-organizer-frontend`: логин + вкладки Расписание/Брони/Настройки, зеркалит event-admin-frontend auth-flow + booker-frontend).
2. **Типы событий, Календари** — у организатора-референса (Test User) их нет; типы/подключение календарей — админ/admin-frontend или отдельные срезы.
3. **TOTP/2FA, login-guard/rate-limit, сброс пароля (email), self-регистрация, смена email, bio/аватар/telegram/язык/внешний-вид** — отдельные hardening/feature-срезы.
4. **Slice-5 SSRF hardening** (iCal fetch) — отдельный pre-prod блокер, не часть 6.1.

## 9. Определение готовности 6.1
- Сервис `event-organizer` поднимается (`/health`,`/ready`,`/metrics`); миграция 0001; своя БД.
- Пароль-логин (bcrypt+JWT), admin-провижининг (проверка organizer в event-users, dedup).
- `/api/me/*`: расписание (GET/PUT/travel), брони (read), профиль (GET/PUT name+tz), смена пароля — **id всегда из сессии**.
- Секреты серверно; ошибки без утечек; тесты §7 зелёные; ruff clean.
- Docker + docker-compose (host 8006, deps event-scheduling+event-users+postgres) + `event-organizer/CLAUDE.md` + корневой CLAUDE.md (сервис+порт) + `docs/architecture/ONBOARDING.md`/`ARCHITECTURE.md` (новый BFF, organizer-auth, ownership by construction — закрывает slice-5 IDOR).
