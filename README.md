# events

Событийная (event-driven) система для управления бронированиями и участниками встреч.
Точка входа — вебхуки от **cal.com**: они проходят через цепочку независимых сервисов,
которые сохраняют данные, создают сущности во внешних сервисах (чаты, видеовстречи,
короткие ссылки), рассылают уведомления и ведут аудит.

Монорепозиторий объединяет **10 сервисов**. Каждый сервис — отдельный git-репозиторий со
своим `CLAUDE.md` (команды, архитектура) и каталогом `docs/`. Кросс-сервисная документация
и общий запуск (docker-compose) живут в корне.

> Для ассистентов и подробных соглашений см. корневой [`CLAUDE.md`](CLAUDE.md).
> Архитектура и C4-диаграммы — [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md),
> онбординг — [`docs/architecture/ONBOARDING.md`](docs/architecture/ONBOARDING.md).

## Сервисы

| Сервис | Стек | Роль |
|---|---|---|
| `event-receiver` | Python, FastAPI | Ингресс: валидирует вебхуки (вкл. cal.com `POST /event/calcom`), оборачивает payload в конверт `{original, normalized}`, публикует CloudEvents в RabbitMQ |
| `event-saver` | Python, FastAPI, FastStream | Консьюмер RabbitMQ; **владеет** основной базой PostgreSQL и пишет в неё |
| `event-booking` | Python, FastAPI, FastStream | Оркестратор бронирований: лимиты и чёрные списки, чтение/запись БД cal.com, GetStream-чаты, Jitsi-ссылки, напоминания, публикация follow-up событий |
| `event-admin` | Python, FastAPI | Read-only API над БД event-saver; публикует админ-действия через event-receiver |
| `event-admin-frontend` | TypeScript, React, Vite | Админ-панель: бронирования, участники, чёрный список |
| `event-users` | Python, FastAPI | Управление пользователями и контактами, синхронизация с CRM; консьюмер `events.user.email` |
| `event-notifier` | Python, FastAPI, FastStream | Доставка уведомлений: outbox → email (UniSender) / Telegram, публикация результатов доставки |
| `event-shortener` | Python, FastAPI | REST-сократитель ссылок (своя PostgreSQL); event-booking сокращает через него ссылки на встречи |
| `event-schemas` | Python, Pydantic | Общая библиотека схем: payload'ы, конверт, **каноническая топология RabbitMQ**; не рантайм-сервис |
| `jitsi-chat` | TypeScript, React, Vite | SPA участника: видеовстреча Jitsi + чат Stream |

## Поток данных

```
cal.com webhooks / внешние клиенты        jitsi-chat SPA (события Jitsi-iframe)
        │                                       │
        ▼                                       ▼
  event-receiver        (валидация, нормализация → CloudEvent {original, normalized})
        │ RabbitMQ topic exchange "events" (DLX: events.dlx)
        │
        ├──► events.booking.lifecycle.saver ──► event-saver  (пишет PostgreSQL)
        │                                            ├──► event-admin (read-only API)
        │                                            │         └──► event-admin-frontend
        │                                            └──► [events, bookings, participants, projections]
        │
        ├──► events.booking.lifecycle.booking ──► event-booking
        │         (БД cal.com; GetStream-чаты; Jitsi-ссылки; напоминания)
        │         ├──► REST ──► event-shortener (короткие ссылки; своя PostgreSQL)
        │         └──► follow-up события ──► HTTP POST обратно в event-receiver
        │
        ├──► events.notification.commands ──► event-notifier ──► UniSender / Telegram
        │
        └──► events.user.email ──► event-users (своя БД: users, user_contacts; CRM-sync)
```

Контракты сообщений (типы CloudEvent, конверт, очереди) — единый источник истины в
`event-schemas`; см. [`docs/architecture/MESSAGE_CONTRACTS.md`](docs/architecture/MESSAGE_CONTRACTS.md).

## Быстрый старт (Docker Compose)

Весь контур — 10 сервисов, RabbitMQ, 5 экземпляров PostgreSQL и WireMock-заглушки для
оставшихся внешних HTTP API — поднимается одной командой из корня репозитория. `.env` не
обязателен: dev-значения по умолчанию вшиты в `docker-compose.yml`.

```bash
docker compose up -d --build                            # 10 сервисов + инфраструктура
docker compose --profile observability up -d --build    # + Prometheus / Grafana / Alertmanager
cp .env.example .env                                    # опционально: переопределить нужное
docker compose down -v                                  # остановить и удалить тома
```

Стек наблюдаемости вынесен в профиль **`observability`** и по умолчанию выключен. Включить —
второй командой выше или `COMPOSE_PROFILES=observability` в `.env`.

### Порты на хосте

| Порт | Сервис |
|---|---|
| 8888 | event-receiver (вебхуки: `/event/calcom`, `/event/jitsi`, …) |
| 8001 | event-users API |
| 8002 | event-admin API |
| 8000 | event-shortener API |
| 3000 | event-admin-frontend |
| 8080 | jitsi-chat SPA |
| 8089 | WireMock-моки (журнал: `/__admin/requests`) |
| 5672 / 15672 | RabbitMQ (AMQP / management UI) |
| 5433 | pg-calcom (фикстурная БД cal.com) |
| 9090 / 3001 / 9093 | Prometheus / Grafana / Alertmanager *(профиль observability)* |
| 9428 | VictoriaLogs *(профиль observability; логи контейнеров от Vector; LogsQL UI + API)* |
| 3200 | Tempo *(профиль observability; 127.0.0.1; хранилище трейсов; datasource Grafana)* |
| 4317 | OTel Collector *(профиль observability; 127.0.0.1; OTLP/gRPC; сервисы → коллектор → Tempo)* |

### Симуляция событий cal.com

`scripts/calcom_sim.py` генерирует реалистичные подписанные вебхуки cal.com и пишет
фикстурные строки в pg-calcom — удобно для отладки всей цепочки:

```bash
uv run scripts/calcom_sim.py create [--starts-in 1h] [--locale en] [--dry-run]
uv run scripts/calcom_sim.py lifecycle          # created → rescheduled → cancelled
uv run scripts/calcom_sim.py cancel <uid>
uv run scripts/calcom_sim.py reschedule <uid>
```

### Внешние API и БД cal.com

- **Моки vs реальные API**: UniSender Go, Telegram Bot API и GetStream по умолчанию идут в
  WireMock (`docker/mocks/mappings/`). Укажите реальные `*_URL`/ключи в `.env`, чтобы
  интегрироваться по-настоящему. Сокращение ссылок — реальный сервис `event-shortener`.
- **Внешний cal.com**: по умолчанию event-booking читает засеянную фикстуру `pg-calcom`
  (`docker/calcom-init/`). Задайте `CALCOM_DATABASE_URL` в `.env`, чтобы подключить
  настоящий инстанс cal.com.

## Наблюдаемость

В профиле `observability` каждый Python-сервис отдаёт `GET /metrics` (prometheus-client),
Prometheus собирает метрики сервисов + RabbitMQ + postgres-экспортёров. Grafana
провижинит дашборды (`events-system-overview`, `events-booking-flow`, `events-logs`). Alertmanager
маршрутизирует алерты (`docker/prometheus/rules/`) в Telegram — задайте
`ALERT_TELEGRAM_BOT_TOKEN` / `ALERT_TELEGRAM_CHAT_ID` в `.env` для реальной доставки.
Логи: **Vector** собирает stdout всех контейнеров через Docker-сокет в **VictoriaLogs**
(хранение 7 дней), доступно в Grafana через datasource `victorialogs` и дашборд Logs
(конфиг — `docker/vector/vector.yaml`).
Трейсинг: каждый Python-сервис экспортирует OpenTelemetry-спаны через OTLP/gRPC в
**OTel Collector** → **Tempo** (datasource uid `tempo`). По умолчанию выключен
(`OTEL_SDK_DISABLED=true`); для включения запустите профиль с
`OTEL_SDK_DISABLED=false docker compose --profile observability up -d --build`.
Конфиги — `docker/tempo/tempo.yaml`, `docker/otel-collector/config.yaml`.
Подробности — [`docs/architecture/ONBOARDING.md`](docs/architecture/ONBOARDING.md) § Observability.

## Production (Kubernetes)

Docker Compose — для локальной разработки; для production есть Kubernetes-инфраструктура
в каталоге [`deploy/`](deploy/):

- **Helm** ([`deploy/helm/`](deploy/helm/)) — библиотечный чарт `events-common`, тонкие
  per-service чарты, umbrella-чарты `events-platform` (9 сервисов) и `events-observability`
  (kube-prometheus-stack + VictoriaLogs + Vector). Конфиг и секреты — только из Vault через
  External Secrets Operator (ConfigMap'ы значений не хранят).
- **ArgoCD app-of-apps** ([`deploy/argocd/`](deploy/argocd/)) — GitOps-развёртывание
  prerequisites и обоих umbrella по sync-wave (cert-manager → ingress-nginx/vault → ESO →
  platform/observability).
- **Скрипты** ([`deploy/scripts/`](deploy/scripts/)) — `Makefile` (`lint`/`template`/
  `bootstrap`/`seed`/`smoke`/`clean`) + `smoke.sh` (kind-смоук) + `seed-vault.sh`.
- **CI**: каждый деплоимый сервис собирает и пушит образ `ghcr.io/lelikov/<service>` через
  GitHub Actions (`publish-image.yml`) **и** GitLab CI (`.gitlab-ci.yml`).

Порядок развёртывания и нюанс с инициализацией Vault — см.
[`docs/architecture/ONBOARDING.md`](docs/architecture/ONBOARDING.md) § «Deploying to Kubernetes»
и [`deploy/argocd/README.md`](deploy/argocd/README.md).

## Технические соглашения

**Python-сервисы**: Python 3.14, `uv`, FastAPI, Dishka (DI), FastStream (консьюмеры),
сырой `text()` SQL через `SqlExecutor` (ORM — только для Alembic), Protocol-интерфейсы,
frozen dataclass DTO, ruff (line length 120). Стиль: без `elif`, минимум `else` —
ранние возвраты / guard-clauses / маппинги. Liveness `GET /health`, readiness `GET /ready`.

**Фронтенды**: TypeScript, React, Vite.

**Владение БД**: event-saver владеет миграциями основной БД; event-admin — read-only;
event-users / event-notifier / event-shortener владеют своими БД; event-booking пишет в БД
cal.com, но НЕ мигрирует её (схемой владеет cal.com).

## Документация

- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) — топология, C4, решения
- [`docs/architecture/MESSAGE_CONTRACTS.md`](docs/architecture/MESSAGE_CONTRACTS.md) — контракты CloudEvent между сервисами
- [`docs/architecture/CODING_STANDARDS.md`](docs/architecture/CODING_STANDARDS.md) — общие соглашения
- [`docs/architecture/ONBOARDING.md`](docs/architecture/ONBOARDING.md) — онбординг, запуск, наблюдаемость
- [`docs/audit/`](docs/audit/) — аудит системы, граф зависимостей, scalability gaps
- `docs/superpowers/specs/` — дизайн-спеки реализованных фич

У каждого сервиса — собственный `CLAUDE.md` и `docs/` с деталями (SERVICE_OVERVIEW,
API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT).
