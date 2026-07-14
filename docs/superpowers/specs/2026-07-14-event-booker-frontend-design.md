# event-booker-frontend — public Booker SPA (срез 4b.2)

> Дизайн-спек. Дата: 2026-07-14.
> Публичная React-SPA поверх BFF `event-booker` (срез 4b.1): гость выбирает тип встречи → слот → вводит имя+email → получает подтверждение. Аддитивно — новый пакет, существующие сервисы не трогаем.

## 0. Контекст и рамка

**Что уже есть (срез 4b.1):** BFF `event-booker` (host 8005, internal 8888) с 4 публичными (без auth) эндпоинтами:
- `GET /api/public/event-types` → `{"items":[{"id","slug","title","duration_minutes"}]}`
- `GET /api/public/event-types/{id}` → `{"id","slug","title","duration_minutes"}` (404 если нет)
- `GET /api/public/slots?event_type_id=&start=&end=&time_zone=` → `{"event_type_id","time_zone","slots":{"<date>":["<iso-datetime>", …]}}`
- `POST /api/public/bookings` body `{"event_type_id","name","email","start_time","time_zone"}` → 201 `{"booking_id","event_type_title","start_time","end_time","status","time_zone"}`; ошибки: 409 (слот занят), 422 (валидация/плохой email), 404 (нет типа), 502 (upstream). Тело ошибки — `{"detail": "<message>"}`.

**Задача 4b.2:** публичная SPA `event-booker-frontend` — полный booker (список типов → визард бронирования). **Только фронтенд** — BFF и остальные сервисы не меняем.

**Границы:** охват — полный (подход A): главная со списком типов → страница бронирования (визард из 3 шагов: слот → данные → подтверждение). Cancel/reschedule/«моя бронь»/оплата/auth — **вне рамок** (у BFF нет соответствующих эндпоинтов; подтверждение терминально).

### Ключевые факты (разведка монорепо — образец: `event-admin-frontend`)
- **Стек:** React 19 + Vite 8 + TypeScript, **plain CSS** (без Tailwind/styled/emotion), **рукописный роутер** (`modules/shared/routing.ts`: `parseRoute`/`navigateTo` + `popstate`/`app:navigate` — **react-router в монорепо НЕ используется**), **vitest + happy-dom**, gated **Sentry** (`observability/sentry.ts`, off by default, `window._env_`).
- **Структура:** `src/modules/<feature>/` (компоненты + `*Api.ts`), `src/modules/shared/` (`api.ts` fetch-wrapper с `ApiError`+`parseErrorDetail`, `runtimeEnv.ts` `getEnv`, `routing.ts`, `ErrorBoundary.tsx`), `src/observability/sentry.ts`, `src/main.tsx` (`initSentry()` + `createRoot`), `src/App.tsx` (роут-свитч).
- **`api.ts`:** `apiRequest<T>(path, {method, body, auth, baseUrl})`; `ApiError{status, code, details, message}`; `parseErrorDetail` уже понимает `{"detail": "<string>"}` (ровно формат ошибок BFF). Для booker'а — **выкинуть JWT/auth** (публичный контур).
- **`getEnv`:** `window._env_[key]` (runtime, из `docker-entrypoint.d/40-env-config.sh`) → фоллбэк `import.meta.env[key]`.
- **Деплой:** `Dockerfile` (node:22-alpine build → nginx:alpine), `nginx.conf` (SPA `try_files` + `location /api/` proxy к бэку + `/health` отвечает nginx сам), `docker-entrypoint.d/40-env-config.sh` (пишет `window._env_` из `VITE_*`). docker-compose: `${..._PORT:-3000}:80`, healthcheck `wget 127.0.0.1:80/health`.
- **Свободный host-порт:** 3000 admin-frontend, 3001 Grafana → booker-frontend = **3002**.
- Пакет `event-booker-frontend` — трекается КОРНЕВЫМ репо.

## 1. Архитектура

```
public browser ──(same-origin)──► nginx (event-booker-frontend, :3002→80)
                                     ├── /            → SPA (try_files → index.html)
                                     ├── /api/*       → proxy → event-booker:8888
                                     └── /health      → nginx 200
SPA (React):
  main.tsx → App.tsx (parseRoute)
    ├── EventTypeListPage   ("/")               GET /api/public/event-types
    └── BookingFlowPage     ("/book/{id}")      3-step wizard
          step 1 SlotPicker    GET /event-types/{id} + GET /slots
          step 2 GuestForm     name + email
          step 3 Confirmation  POST /bookings → confirmation
```

**Модули (каждый — одна ответственность):**
- `src/modules/booking/bookerApi.ts` — публичный API-клиент (4 функции).
- `src/modules/booking/types.ts` — TS-типы контракта BFF.
- `src/modules/booking/EventTypeListPage.tsx` — главная: карточки типов встреч → `/book/{id}`.
- `src/modules/booking/BookingFlowPage.tsx` — визард-оркестратор (шаги, состояние выбранного слота/данных/tz, сабмит).
- `src/modules/booking/SlotPicker.tsx` — окно дат + слоты, tz-селектор, выбор времени.
- `src/modules/booking/GuestForm.tsx` — имя + email + клиентская валидация.
- `src/modules/booking/Confirmation.tsx` — экран подтверждения.
- `src/modules/booking/datetime.ts` — форматирование дат/времени в выбранной tz (`Intl.DateTimeFormat`).
- `src/modules/shared/api.ts` — `apiRequest` (публичный, без JWT) + `ApiError` + `parseErrorDetail`.
- `src/modules/shared/runtimeEnv.ts`, `routing.ts`, `ErrorBoundary.tsx` — по образцу admin (адаптированы).
- `src/observability/sentry.ts` — gated Sentry (копия admin).
- `src/App.tsx`, `src/main.tsx`, `src/index.css`, `src/App.css`.

## 2. Экраны и поток

**Главная `/` — EventTypeListPage:** на монтировании `listEventTypes()`; loading → spinner; error → сообщение + «повторить»; успех → карточки (title, «N мин»), клик → `navigateTo('/book/{id}')`. Пустой список → «нет доступных типов встреч».

**Бронирование `/book/{event_type_id}` — BookingFlowPage (визард):**
- **Шаг 1 — SlotPicker:** при входе `getEventType(id)` (заголовок/длительность; 404 → «тип встречи не найден») + `getSlots(id, windowStart, windowEnd, tz)`. tz по умолчанию — `Intl.DateTimeFormat().resolvedOptions().timeZone`; редактируется селектором (курируемый список + определённая tz). Окно — `[now, now+14 дней]` (конфиг-константа), кнопка «позже» сдвигает окно на следующие 14 дней (BFF/scheduling кап — 62 дня). Рендер: даты с доступными слотами; выбор даты → список времён (форматируется в tz); выбор времени сохраняет `start_time` (строка-слот as-is) → «далее».
- **Шаг 2 — GuestForm:** поля имя (required) + email (required, клиентская regex-валидация) + сводка выбранного слота; «назад»/«далее».
- **Шаг 3 — сабмит + Confirmation:** `createBooking({event_type_id, name, email, start_time, time_zone})`. 201 → Confirmation (`event_type_title`, дата/время начала-конца в tz, статус). Ошибки:
  - **409** → назад на Шаг 1 + баннер «слот только что заняли, выберите другой» + рефетч слотов.
  - **422** → показать `detail` у формы (Шаг 2).
  - **404** (тип исчез) → сообщение + ссылка на главную.
  - **502/сеть** → «сервис временно недоступен, попробуйте ещё раз» (кнопка повтора; данные формы сохранены).

**Неизвестный путь → NotFound** (ссылка на главную).

## 3. API-клиент (`bookerApi.ts`)

Публичный `apiRequest` (из `shared/api.ts`, `auth` убран). Относительные пути (`/api/public/...`) → same-origin nginx-proxy; в dev — Vite proxy на `event-booker`. Функции:
- `listEventTypes(): Promise<EventType[]>` → `GET /api/public/event-types` → `.items`.
- `getEventType(id: string): Promise<EventType>` → `GET /api/public/event-types/{id}`.
- `getSlots(eventTypeId, startISO, endISO, timeZone): Promise<Slots>` → `GET /api/public/slots?...`.
- `createBooking(body: CreateBookingBody): Promise<BookingConfirmation>` → `POST /api/public/bookings`.

**Типы (`types.ts`):** `EventType{id, slug, title, duration_minutes}`; `Slots{event_type_id, time_zone, slots: Record<string, string[]>}`; `CreateBookingBody{event_type_id, name, email, start_time, time_zone}`; `BookingConfirmation{booking_id, event_type_title, start_time, end_time, status, time_zone}`. Все — из контракта BFF 4b.1 (сверено).

## 4. Обработка ошибок и загрузки
- Каждый fetch: явные состояния loading / error / success; сообщения — по-русски, из `ApiError.message` (BFF `detail`) или дефолт по статусу.
- `ErrorBoundary` оборачивает приложение (креш → fallback-экран, не белый лист).
- 409 — специальный UX (рефетч + возврат к слотам), не общий error.
- Никакого доверия к клиентской валидации на стороне безопасности — BFF/scheduling всё равно валидируют; клиентская проверка email/required только для UX.

## 5. Наблюдаемость
- `observability/sentry.ts` (копия admin): gated `VITE_SENTRY_ENABLED` + `VITE_SENTRY_DSN`, off by default, DSN через `window._env_`. `initSentry()` в `main.tsx`. `@sentry/react` + `@sentry/vite-plugin` (source maps в CI, как у admin — опционально).

## 6. Деплой
- `Dockerfile` — node:22-alpine build (`npm ci` + `npm run build`) → nginx:alpine (dist + nginx.conf + env-config entrypoint). `ARG VITE_API_BASE_URL=""` (пусто = same-origin).
- `nginx.conf` — `location /` try_files SPA; `location /api/` proxy → `event-booker:8888`; `location = /health` → nginx `200 "ok"`. Проще admin-конфига: пути SPA (`/`, `/book/*`) и API (`/api/*`) не пересекаются → `Vary: Accept` не нужен.
- `docker-entrypoint.d/40-env-config.sh` — как у admin (`window._env_` из `VITE_*`).
- `vite.config.ts` — dev-proxy `/api` → `${VITE_API_BASE_URL || 'http://localhost:8005'}` (event-booker host-порт); Sentry-plugin (gated by `SENTRY_AUTH_TOKEN`).
- `docker-compose.services.yml` — сервис `event-booker-frontend`: build context `./event-booker-frontend`, `${BOOKER_FRONTEND_PORT:-3002}:80`, `depends_on: event-booker (service_healthy)`, healthcheck `wget 127.0.0.1:80/health`.

## 7. Тестирование (vitest + happy-dom)
- **`bookerApi`** (mock `fetch`): каждая функция строит верный путь/метод/тело и парсит ответ; не-2xx → `ApiError` со `status` и `message` из `detail` (409/422/404/502).
- **`SlotPicker`**: по данным слотов рендерит даты и времена; выбор времени → колбэк с нужным `start_time`; смена tz → рефетч; кнопка «позже» сдвигает окно.
- **`GuestForm`**: пустое имя/невалидный email → ошибка, submit заблокирован; валидные данные → колбэк.
- **`BookingFlowPage`** (mock `bookerApi`): переходы слот→данные→подтверждение; 409 на сабмите → возврат к Шагу 1 с баннером; 422 → ошибка у формы.
- **`EventTypeListPage`** (mock `bookerApi`): рендер карточек; клик → `navigateTo`.
- **`routing`** (`parseRoute`): `/`→event-types, `/book/{id}`→book c id, прочее→not-found.
- **`sentry`**: копия admin-теста (gated init).
- `npm run test` (vitest) зелёный; `npm run lint` (eslint) чистый; `npm run build` (tsc -b + vite build) успешен.

## 8. Отложено / вне рамок
1. **Cancel / reschedule / «моя бронь»** — у BFF нет endpoint'ов (POST-only); нужен подписанный booking-token — отдельный срез (вместе с BFF-расширением).
2. **Оплата, кастомные поля брони, несколько участников** — вне рамок.
3. **i18n-фреймворк** — UI-копия по-русски инлайн (как весь монорепо); мультиязычность позже.
4. **Продвинутый календарь** (месячная сетка, таймзон-детект по геолокации) — минимальный список дат/времён; улучшение позже.
5. **Helm chart** для frontend — как у `event-admin-frontend` (по шаблону), быстрый follow-up; в 4b.2 — Dockerfile + docker-compose (dev-контур).

## 9. Определение готовности 4b.2
- Пакет `event-booker-frontend` поднимается в dev-контуре (nginx :3002 → event-booker); гость проходит поток: главная → выбор типа → слот → имя+email → подтверждение; бронь реально создаётся через BFF (client в event-users, бронь в event-scheduling).
- Ошибки: 409 → возврат к слотам с баннером; 422 → ошибка формы; 404 → сообщение; 502/сеть → повтор.
- `npm run test` + `npm run lint` + `npm run build` зелёные; ничего в существующих сервисах не сломано.
- Docker + docker-compose (порт 3002) + `event-booker-frontend/CLAUDE.md` + корневой `CLAUDE.md` (сервис-таблица + порт 3002) + `docs/architecture/ONBOARDING.md` (booker frontend).
- gated Sentry (off by default); tz авто-детект + селектор; UI-копия по-русски.
