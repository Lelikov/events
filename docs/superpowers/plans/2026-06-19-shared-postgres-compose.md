# Единый Postgres в docker-compose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Свести 5 отдельных app-Postgres контейнеров в docker-compose к одному общему `postgres`-инстансу с 5 БД + per-service ролями (как в проде); cal.com остаётся отдельным.

**Architecture:** Один контейнер `postgres` с init-скриптом (`docker/postgres-init/00-init-databases.sh`), создающим 5 БД + 5 login-ролей-владельцев из env. Все app-сервисы ходят на хост `postgres` со своими БД/логином/паролем; `pg-calcom` без изменений. Exporters консолидируются в 2.

**Tech Stack:** Docker Compose, postgres:16, prometheus postgres-exporter. Один репозиторий — root `events`.

**Spec:** `docs/superpowers/specs/2026-06-19-shared-postgres-compose-design.md`

---

## Conventions

- Всё в root-репозитории `events`. Перед стартом создать ветку `feat/shared-postgres-compose` (репо на `main`):
  ```bash
  cd /Users/alexandrlelikov/PycharmProjects/events && git checkout -b feat/shared-postgres-compose
  ```
- Сообщения коммитов заканчиваются:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **НЕ пушить** (push/merge — отдельный user-gated шаг).
- Это инфра-конфиг (не Python) — «тест» = валидация: `docker compose config -q` (парсинг). Если Docker недоступен — fallback: `python3 -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml'))"` + grep-проверки. Отмечать, какой способ использован.

## Канонические значения (использовать ДОСЛОВНО)

| Сервис | хост (после) | БД | роль/пароль (default) |
|---|---|---|---|
| общий инстанс | `postgres` | — | superuser `${PG_SUPERUSER:-postgres}` |
| event-saver / event-admin | `postgres` | `event_saver` | `event_saver` |
| event-users | `postgres` | `event_users` | `event_users` |
| event-notifier | `postgres` | `event_notifier` | `event_notifier` |
| event-shortener | `postgres` | `event_shortener` | `event_shortener` |
| event-db-sync (own) | `postgres` | `event_db_sync` | `event_db_sync` |
| cal.com (booking, db-sync) | `pg-calcom` | `calcom` | `calcom` (без изменений) |

---

### Task 1: init-скрипт общего инстанса

**Files:**
- Create: `docker/postgres-init/00-init-databases.sh`

- [ ] **Step 1: Создать скрипт** `docker/postgres-init/00-init-databases.sh`:
```sh
#!/bin/bash
# Provisions per-service databases + login roles on the shared dev Postgres
# instance, mirroring the prod managed-PG model (one host, per-DB login).
# Runs once on a fresh data volume via /docker-entrypoint-initdb.d.
set -euo pipefail

create_db_role() {
  local db="$1" user="$2" pass="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    DO \$\$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$user') THEN
        CREATE ROLE "$user" LOGIN PASSWORD '$pass';
      END IF;
    END \$\$;
    SELECT 'CREATE DATABASE "$db" OWNER "$user"'
      WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
    GRANT ALL PRIVILEGES ON DATABASE "$db" TO "$user";
SQL
  echo "  provisioned db=$db owner=$user"
}

echo "Provisioning shared-instance databases + roles..."
create_db_role "${PG_SAVER_DB:-event_saver}"         "${PG_SAVER_USER:-event_saver}"         "${PG_SAVER_PASSWORD:-event_saver}"
create_db_role "${PG_USERS_DB:-event_users}"         "${PG_USERS_USER:-event_users}"         "${PG_USERS_PASSWORD:-event_users}"
create_db_role "${PG_NOTIFIER_DB:-event_notifier}"   "${PG_NOTIFIER_USER:-event_notifier}"   "${PG_NOTIFIER_PASSWORD:-event_notifier}"
create_db_role "${PG_SHORTENER_DB:-event_shortener}" "${PG_SHORTENER_USER:-event_shortener}" "${PG_SHORTENER_PASSWORD:-event_shortener}"
create_db_role "${PG_DB_SYNC_DB:-event_db_sync}"     "${PG_DB_SYNC_USER:-event_db_sync}"     "${PG_DB_SYNC_PASSWORD:-event_db_sync}"
echo "Done."
```

- [ ] **Step 2: Сделать исполняемым + проверить синтаксис**
```bash
chmod +x docker/postgres-init/00-init-databases.sh
bash -n docker/postgres-init/00-init-databases.sh && echo "SYNTAX OK"
```
Expected: `SYNTAX OK`. (`.sh` в `docker-entrypoint-initdb.d` имеет доступ к env контейнера `postgres`, который мы зададим в Task 2. Defaults `:-event_saver` дублируют compose-дефолты, чтобы bare-up без `.env` тоже создавал per-service роли.)

- [ ] **Step 3: Commit**
```bash
git add docker/postgres-init/00-init-databases.sh
git commit -m "feat(compose): init script for shared Postgres (per-service DBs + roles)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: docker-compose.yml — общий `postgres`-сервис, удалить 5 pg-*, тома

**Files:**
- Modify: `docker-compose.yml` (pg-* сервисы ~строки 39-124; volumes ~771-775)

- [ ] **Step 1: Добавить сервис `postgres`** — на место удаляемого блока `pg-saver` (первым среди pg-сервисов) вставить (env содержит ВСЕ PG_* — их читает init-скрипт):
```yaml
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: ${PG_SUPERUSER:-postgres}
      POSTGRES_PASSWORD: ${PG_SUPERUSER_PASSWORD:-postgres}
      POSTGRES_DB: ${PG_SUPERUSER_DB:-postgres}
      PG_SAVER_USER: ${PG_SAVER_USER:-event_saver}
      PG_SAVER_PASSWORD: ${PG_SAVER_PASSWORD:-event_saver}
      PG_SAVER_DB: ${PG_SAVER_DB:-event_saver}
      PG_USERS_USER: ${PG_USERS_USER:-event_users}
      PG_USERS_PASSWORD: ${PG_USERS_PASSWORD:-event_users}
      PG_USERS_DB: ${PG_USERS_DB:-event_users}
      PG_NOTIFIER_USER: ${PG_NOTIFIER_USER:-event_notifier}
      PG_NOTIFIER_PASSWORD: ${PG_NOTIFIER_PASSWORD:-event_notifier}
      PG_NOTIFIER_DB: ${PG_NOTIFIER_DB:-event_notifier}
      PG_SHORTENER_USER: ${PG_SHORTENER_USER:-event_shortener}
      PG_SHORTENER_PASSWORD: ${PG_SHORTENER_PASSWORD:-event_shortener}
      PG_SHORTENER_DB: ${PG_SHORTENER_DB:-event_shortener}
      PG_DB_SYNC_USER: ${PG_DB_SYNC_USER:-event_db_sync}
      PG_DB_SYNC_PASSWORD: ${PG_DB_SYNC_PASSWORD:-event_db_sync}
      PG_DB_SYNC_DB: ${PG_DB_SYNC_DB:-event_db_sync}
    volumes:
      - pg-data:/var/lib/postgresql/data
      - ./docker/postgres-init:/docker-entrypoint-initdb.d:ro
    ports:
      - "127.0.0.1:${PG_PORT:-5432}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${PG_SUPERUSER:-postgres}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped
```

- [ ] **Step 2: Удалить** блоки сервисов `pg-saver`, `pg-users`, `pg-notifier`, `pg-shortener`, `pg-db-sync` (целиком, вместе с их комментариями-заголовками). **Оставить `pg-calcom` без изменений.**

- [ ] **Step 3: Тома** — в top-level `volumes:` удалить `pg-saver-data:`, `pg-users-data:`, `pg-notifier-data:`, `pg-shortener-data:`, `pg-db-sync-data:`; добавить `pg-data:`. Оставить `pg-calcom-data:`.

- [ ] **Step 4: Валидация**
```bash
docker compose config -q 2>&1 && echo "COMPOSE OK" || python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('YAML OK')"
grep -c 'pg-saver\|pg-users\|pg-notifier\|pg-shortener\|pg-db-sync' docker-compose.yml
```
Expected: `COMPOSE OK` (или `YAML OK`). Второй grep на удалённые pg-* имена сейчас вернёт НЕ ноль (остались ссылки в app-сервисах `depends_on`/DSN — их чинит Task 3). Это ожидаемо; полную валидность проверим в конце Task 3.

> ПРИМЕЧАНИЕ: после этого шага `docker compose config` может ругаться на `depends_on: pg-saver` в app-сервисах (ссылка на несуществующий сервис). Если так — это нормально, Task 3 их перенаправит. Если валидатор падает жёстко на этом, закоммить как есть и сразу переходи к Task 3 (они логически атомарны); при ревью контроллер учтёт. (Альтернатива: выполнять Task 2 и Task 3 одним подходом — допустимо.)

- [ ] **Step 5: Commit**
```bash
git add docker-compose.yml
git commit -m "feat(compose): single shared postgres service; drop per-service pg-* containers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: docker-compose.yml — перенаправить app-сервисы на `postgres`

**Files:**
- Modify: `docker-compose.yml` (DSN + depends_on у 6 app-сервисов; pg-calcom-зависимые не трогаем по cal.com)

- [ ] **Step 1: DSN — заменить хост `pg-<svc>` → `postgres` и дефолты логина/пароля `:-postgres` → per-service.** Точечные замены (по уникальным строкам):
  - event-saver: `...${PG_SAVER_USER:-postgres}:${PG_SAVER_PASSWORD:-postgres}@pg-saver:5432/${PG_SAVER_DB:-event_saver}` → `...${PG_SAVER_USER:-event_saver}:${PG_SAVER_PASSWORD:-event_saver}@postgres:5432/${PG_SAVER_DB:-event_saver}`
  - event-admin (читает saver-БД): та же строка `POSTGRES_DSN` с `PG_SAVER_*` → так же `@postgres` + дефолты `event_saver`.
  - event-users: `${PG_USERS_USER:-postgres}:${PG_USERS_PASSWORD:-postgres}@pg-users:5432/...` → `${PG_USERS_USER:-event_users}:${PG_USERS_PASSWORD:-event_users}@postgres:5432/...`
  - event-notifier: `${PG_NOTIFIER_USER:-postgres}:${PG_NOTIFIER_PASSWORD:-postgres}@pg-notifier:5432/...` → `...:-event_notifier...@postgres...`
  - event-shortener: `${PG_SHORTENER_USER:-postgres}:${PG_SHORTENER_PASSWORD:-postgres}@pg-shortener:5432/...` → `...:-event_shortener...@postgres...`
  - event-db-sync `DATABASE_URL`: `${PG_DB_SYNC_USER:-postgres}:${PG_DB_SYNC_PASSWORD:-postgres}@pg-db-sync:5432/...` → `...:-event_db_sync...@postgres...`
  - **НЕ трогать** event-booking `CALCOM_POSTGRES_DSN` и event-db-sync `CALCOM_DATABASE_URL` (они на `pg-calcom`).

- [ ] **Step 2: depends_on — `pg-<svc>` → `postgres`.** В каждом app-сервисе заменить ключ зависимости:
  - event-saver: `pg-saver:` → `postgres:` (condition service_healthy сохранить)
  - event-admin: `pg-saver:` → `postgres:`
  - event-users: `pg-users:` → `postgres:`
  - event-notifier: `pg-notifier:` → `postgres:`
  - event-shortener: `pg-shortener:` → `postgres:`
  - event-db-sync: `pg-db-sync:` → `postgres:` (оставить и `pg-calcom:`)
  - event-booking: `pg-calcom:` — без изменений.
  (Если у какого-то сервиса depends_on перечисляет несколько — заменить только pg-* app-ключ, остальные (rabbitmq, mocks, другие сервисы) не трогать.)

- [ ] **Step 3: Полная валидация** (теперь ссылок на удалённые сервисы быть не должно)
```bash
docker compose config -q 2>&1 && echo "COMPOSE OK" || python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('YAML OK')"
echo "--- остались ли ссылки на удалённые pg-* (ждём 0, кроме pg-calcom/pg-data/postgres-init) ---"
grep -nE 'pg-saver|pg-users|pg-notifier|pg-shortener|pg-db-sync' docker-compose.yml || echo "NONE"
```
Expected: `COMPOSE OK`; второй grep — `NONE` (никаких упоминаний удалённых сервисов/томов; `pg-calcom`, `pg-data`, `postgres-init` — допустимы и под этот grep не попадают).
ВАЖНО: на этом шаге exporters (`pg-exporter-saver` и т.д., строки ~172-220) ВСЁ ЕЩЁ ссылаются на удалённые pg-* — их чинит Task 4. Если `docker compose config` падает из-за exporter `depends_on: pg-saver`, это ожидаемо; можно временно оставить и закрыть в Task 4, либо (предпочтительно) выполнить Task 4 сразу следом и валидировать совместно. Отметь в отчёте, если конфиг не зелёный именно из-за exporters.

- [ ] **Step 4: Commit**
```bash
git add docker-compose.yml
git commit -m "feat(compose): point app services at shared postgres host (per-service creds)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Exporters → 2 + prometheus.yml

**Files:**
- Modify: `docker-compose.yml` (exporters ~строки 172-220)
- Modify: `docker/prometheus/prometheus.yml` (job `postgres` ~строки 62-79)

- [ ] **Step 1: Заменить 5 app-exporter'ов на 1 общий.** Удалить `pg-exporter-saver`, `pg-exporter-users`, `pg-exporter-notifier`, `pg-exporter-shortener` (4 блока) и заменить весь app-exporter-набор одним (оставив `pg-exporter-calcom` без изменений):
```yaml
  pg-exporter:
    image: quay.io/prometheuscommunity/postgres-exporter:v0.17.1
    profiles: ["observability"]
    environment:
      DATA_SOURCE_NAME: postgresql://${PG_SUPERUSER:-postgres}:${PG_SUPERUSER_PASSWORD:-postgres}@postgres:5432/${PG_SUPERUSER_DB:-postgres}?sslmode=disable
      PG_EXPORTER_AUTO_DISCOVER_DATABASES: "true"
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
```
(`AUTO_DISCOVER_DATABASES=true` + superuser → метрики по всем app-БД с лейблом `datname`, включая ранее непокрытый `event_db_sync`.)

- [ ] **Step 2: prometheus.yml** — в job `postgres` заменить 5 таргетов на 2:
```yaml
  - job_name: postgres
    static_configs:
      - targets: ["pg-exporter:9187"]
        labels: { db: app }
      - targets: ["pg-exporter-calcom:9187"]
        labels: { db: calcom }
```
(Сохрани прочие поля job, если есть — `scrape_interval` и т.п.)

- [ ] **Step 3: Валидация**
```bash
docker compose config -q 2>&1 && echo "COMPOSE OK" || python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('YAML OK')"
python3 -c "import yaml; yaml.safe_load(open('docker/prometheus/prometheus.yml')); print('PROM YAML OK')"
grep -nE 'pg-exporter-(saver|users|notifier|shortener)' docker-compose.yml docker/prometheus/prometheus.yml || echo "NONE"
```
Expected: `COMPOSE OK`, `PROM YAML OK`, grep → `NONE` (старые app-exporter'ы исчезли отовсюду). Теперь `docker compose config` должен быть полностью зелёным (нет висящих ссылок на удалённые сервисы).

- [ ] **Step 4: Commit**
```bash
git add docker-compose.yml docker/prometheus/prometheus.yml
git commit -m "feat(observability): consolidate postgres-exporter to shared instance + calcom

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: .env.example

**Files:**
- Modify: `.env.example` (PG_* блок ~строки 69-91; закомментированные порты ~44-45; CALCOM_DATABASE_URL ~99)

- [ ] **Step 1: Per-service логины + superuser.** Заменить блок `PG_*` так, чтобы логины различались (как в проде), и добавить bootstrap-переменные общего инстанса. Целевой вид PG-секции (calcom без изменений):
```
# Shared dev Postgres instance (one container, many DBs — mirrors prod managed PG)
PG_SUPERUSER=postgres
PG_SUPERUSER_PASSWORD=postgres
PG_SUPERUSER_DB=postgres
PG_PORT=5432

# Per-service DB + login role on the shared instance (host: postgres)
PG_SAVER_USER=event_saver
PG_SAVER_PASSWORD=event_saver
PG_SAVER_DB=event_saver

PG_USERS_USER=event_users
PG_USERS_PASSWORD=event_users
PG_USERS_DB=event_users

PG_NOTIFIER_USER=event_notifier
PG_NOTIFIER_PASSWORD=event_notifier
PG_NOTIFIER_DB=event_notifier

PG_SHORTENER_USER=event_shortener
PG_SHORTENER_PASSWORD=event_shortener
PG_SHORTENER_DB=event_shortener

PG_DB_SYNC_USER=event_db_sync
PG_DB_SYNC_PASSWORD=event_db_sync
PG_DB_SYNC_DB=event_db_sync

# cal.com — separate instance (own container), like prod (10.16.0.41)
PG_CALCOM_USER=calcom
PG_CALCOM_PASSWORD=calcom
PG_CALCOM_DB=calcom
```

- [ ] **Step 2: Убрать мёртвые порты** — удалить закомментированные `#PG_SHORTENER_PORT=...` и `#PG_DB_SYNC_PORT=...` (их инстансы удалены; общий порт — `PG_PORT`). Оставить `#PG_CALCOM_PORT=5433` если он был.

- [ ] **Step 3: Ремарка про миграцию** — добавить рядом с PG-секцией комментарий:
```
# NOTE: при переходе со старой схемы (отдельные pg-* контейнеры) выполните
# `docker compose down -v` один раз — init нового общего инстанса отрабатывает
# только на чистом томе pg-data; старые pg-*-data тома можно удалить вручную.
```
Также обнови `CALCOM_DATABASE_URL=...` если в .env.example оно ссылалось на что-то изменённое (оно на `pg-calcom` — оставить как есть).

- [ ] **Step 4: Валидация**
```bash
grep -E 'PG_SUPERUSER|PG_SAVER_USER=event_saver|PG_DB_SYNC_USER=event_db_sync' .env.example && echo "ENV OK"
grep -E 'PG_SHORTENER_PORT|PG_DB_SYNC_PORT' .env.example || echo "DEAD PORTS REMOVED"
```
Expected: `ENV OK`, `DEAD PORTS REMOVED`.

- [ ] **Step 5: Commit**
```bash
git add .env.example
git commit -m "docs(compose): per-service PG logins + shared-instance superuser in .env.example

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: deploy-комментарий + CLAUDE.md

**Files:**
- Modify: `deploy/scripts/seed-vault.sh` (устаревший комментарий «4 app DBs»)
- Modify: `CLAUDE.md` (Quick Start / Host ports — упоминания pg-*)

- [ ] **Step 1: seed-vault.sh** — найти комментарий вида `# 10.16.0.40  Managed PostgreSQL — 4 app DBs on ONE instance (per-DB user)` и поправить число на 5 + добавить `event_db_sync`:
```
#   10.16.0.40     Managed PostgreSQL — 5 app DBs on ONE instance, per-DB login
#                  (event_saver, event_users, event_notifier, event_shortener, event_db_sync)
```
И аналогичную фразу «All four app DBs live on the SAME managed instance» → «All five app DBs …». Логику/DSN не трогать.

- [ ] **Step 2: CLAUDE.md** — в разделе про docker-compose / таблице host-портов обновить упоминания Postgres: вместо набора `pg-saver/users/notifier/shortener/db-sync` — один общий `postgres` (порт `127.0.0.1:5432`) с несколькими БД + отдельный `pg-calcom` (`5433`). Кратко отразить prod-parity (общий инстанс, per-service логины). Если в таблице портов были строки `5436 pg-shortener` / `5437 pg-db-sync` — заменить на `5432 postgres (shared app DBs)`.

- [ ] **Step 3: Валидация** — прочитать изменённые секции, убедиться в связности (нет ссылок на удалённые pg-* как на отдельные инстансы).

- [ ] **Step 4: Commit**
```bash
git add deploy/scripts/seed-vault.sh CLAUDE.md
git commit -m "docs: shared-instance Postgres in compose; fix seed-vault app-DB count

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Live smoke (Docker, CONTROLLER/опционально)

> Требует Docker. Проверяет, что общий инстанс реально провижинит БД+роли и сервисы мигрируют.

- [ ] **Step 1: Чистый старт общего инстанса**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose down -v 2>/dev/null || true
docker compose up -d postgres
sleep 5
docker compose exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -c "\l" | grep -E 'event_saver|event_users|event_notifier|event_shortener|event_db_sync'
docker compose exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -c "\du" | grep -E 'event_saver|event_db_sync'
```
Expected: 5 app-БД и роли присутствуют.

- [ ] **Step 2: Пара сервисов мигрирует и стартует**
```bash
docker compose up -d --build event-saver event-users event-db-sync pg-calcom rabbitmq
sleep 20
docker compose ps | grep -E 'event-saver|event-users|event-db-sync'   # healthy?
docker compose logs event-saver | grep -i 'alembic\|running upgrade\|started' | tail -5
```
Expected: сервисы healthy, миграции прошли под per-service ролью.

- [ ] **Step 3: (observability) exporter покрывает все app-БД**
```bash
docker compose --profile observability up -d pg-exporter
sleep 5
docker compose exec -T pg-exporter sh -c "wget -qO- localhost:9187/metrics" | grep -E 'datname="event_db_sync"' | head -1
```
Expected: метрики с `datname="event_db_sync"` присутствуют (ранее непокрытая БД).

- [ ] **Step 4: Записать результат** в `docs/superpowers/specs/` НЕ нужно; при успехе — короткая заметка контроллеру. Если права/init не сработали — эскалировать.

---

## Self-review

- **Spec coverage:** общий `postgres` + init-роли (T1/T2), репойнт app-DSN+depends_on (T3), cal.com отдельно (не трогаем), exporters→2 + prometheus (T4), .env per-service логины + superuser + down -v ремарка (T5), deploy-комментарий + CLAUDE.md (T6), smoke (T7). ✅
- **Placeholder scan:** конкретный код/YAML во всех шагах; «open risk» (init на чистом томе) вынесен в T5-ремарку и T7-smoke. ✅
- **Type consistency:** хост `postgres`, роли `event_<svc>`, том `pg-data`, exporter `pg-exporter`, `PG_SUPERUSER*` — пишутся одинаково в T1/T2/T3/T4/T5. event-admin использует `PG_SAVER_*`/`event_saver` БД и в T3, и в таблице — согласовано. ✅
- **Порядок валидации:** T2 оставляет временно битые ссылки (depends_on/exporters на pg-*), T3 чинит app-ссылки, T4 чинит exporters → полностью зелёный `docker compose config` достигается к концу T4. Явно отмечено в шагах. ✅
- **cal.com:** ни один шаг не меняет `pg-calcom`/`CALCOM_*` DSN. ✅
