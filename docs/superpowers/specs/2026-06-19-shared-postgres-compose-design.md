# Единый Postgres-инстанс в docker-compose (как в проде) — дизайн

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

Привести основной `docker-compose.yml` к prod-модели managed PostgreSQL: **один общий Postgres-инстанс** (одинаковый host:port) с несколькими БД и **per-service логином/паролем**, вместо отдельного pg-контейнера на каждый сервис. cal.com остаётся отдельным инстансом (как в проде).

## Контекст (текущее состояние)

- `docker-compose.yml` поднимает **6** отдельных контейнеров `postgres:16`: `pg-saver`, `pg-users`, `pg-notifier`, `pg-shortener`, `pg-db-sync`, `pg-calcom` (+ 6 томов `pg-*-data`). Каждый app-сервис ходит в свой `pg-<svc>` по DSN вида `...@pg-<svc>:5432/<db>`.
- **5** sidecar-экспортеров `postgres-exporter` (по одному на pg, КРОМЕ `pg-db-sync` — он не покрыт), профиль `observability`; скрейпятся в `docker/prometheus/prometheus.yml` (job `postgres`, 5 таргетов).
- **prod и kind УЖЕ на общем инстансе:** `deploy/scripts/seed-vault.sh` пинит все app-БД на `10.16.0.40` (per-DB логин), cal.com отдельно на `10.16.0.41`; `deploy/helm/umbrella/events-platform/values-kind.yaml` — один `devpostgresql` с несколькими `CREATE DATABASE`. Расходится только основной compose.
- Кто куда ходит: `pg-saver` делят event-saver (rw) и event-admin (ro, кредами saver); `pg-calcom` делят event-booking и event-db-sync (последний — plain `postgresql://`). Остальные 1:1.
- init-скрипт есть только у `pg-calcom` (`docker/calcom-init/`). У остальных схему накатывает `alembic upgrade head` в entrypoint.

## Решения (из брейнсторма)

1. **Per-service роли** (не общий superuser): init создаёт 5 БД + 5 login-ролей с собственными паролями, каждая роль — OWNER своей БД. Точно зеркалит прод.
2. **Exporters → 2**: один на общий app-инстанс (`PG_EXPORTER_AUTO_DISCOVER_DATABASES=true`) + один на cal.com.
3. **cal.com остаётся отдельным** контейнером.
4. **deploy** по сути не меняется (уже на общем инстансе); только мелкая правка устаревшего комментария.

## Целевая топология

```
postgres (общий app-инстанс, postgres:16)
  superuser ${PG_SUPERUSER}/${PG_SUPERUSER_PASSWORD}, db postgres
  init: docker/postgres-init/00-init-databases.sh → 5 БД + 5 ролей (OWNER)
    event_saver / event_users / event_notifier / event_shortener / event_db_sync
  volume: pg-data ; port 127.0.0.1:5432 ; healthcheck pg_isready

pg-calcom (отдельный, без изменений)
  role calcom, db calcom, init docker/calcom-init/, port 127.0.0.1:5433

app-сервисы:
  event-saver   → postgres / event_saver  (event_saver)
  event-admin   → postgres / event_saver  (event_saver, read-only)
  event-users   → postgres / event_users  (event_users)
  event-notifier→ postgres / event_notifier (event_notifier)
  event-shortener→postgres / event_shortener (event_shortener)
  event-db-sync → postgres / event_db_sync (event_db_sync)  +  pg-calcom (plain DSN)
  event-booking → pg-calcom (CALCOM_POSTGRES_DSN, +asyncpg)
```

## Компоненты

### 1. `docker/postgres-init/00-init-databases.sh` (новый)
Bash-скрипт в `/docker-entrypoint-initdb.d` (`.sh` имеет доступ к env, в отличие от статичного `.sql`). Идемпотентный helper создаёт роль+БД из env-переменных:
```sh
#!/bin/bash
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
}

create_db_role "${PG_SAVER_DB}"     "${PG_SAVER_USER}"     "${PG_SAVER_PASSWORD}"
create_db_role "${PG_USERS_DB}"     "${PG_USERS_USER}"     "${PG_USERS_PASSWORD}"
create_db_role "${PG_NOTIFIER_DB}"  "${PG_NOTIFIER_USER}"  "${PG_NOTIFIER_PASSWORD}"
create_db_role "${PG_SHORTENER_DB}" "${PG_SHORTENER_USER}" "${PG_SHORTENER_PASSWORD}"
create_db_role "${PG_DB_SYNC_DB}"   "${PG_DB_SYNC_USER}"   "${PG_DB_SYNC_PASSWORD}"
```
(Идемпотентность через `IF NOT EXISTS` / `\gexec` — на случай ручного повторного прогона; в норме `docker-entrypoint-initdb.d` отрабатывает один раз на чистом томе.)

### 2. `docker-compose.yml`
- **Добавить** сервис `postgres` (postgres:16): env `POSTGRES_USER=${PG_SUPERUSER:-postgres}`, `POSTGRES_PASSWORD=${PG_SUPERUSER_PASSWORD:-postgres}`, `POSTGRES_DB=${PG_SUPERUSER_DB:-postgres}`; volume `pg-data:/var/lib/postgresql/data`; mount `./docker/postgres-init:/docker-entrypoint-initdb.d:ro`; port `127.0.0.1:${PG_PORT:-5432}:5432`; healthcheck `pg_isready -U ${PG_SUPERUSER:-postgres}` (interval 5s/timeout 5s/retries 10); `restart: unless-stopped`.
- **Удалить** сервисы `pg-saver`, `pg-users`, `pg-notifier`, `pg-shortener`, `pg-db-sync` и их тома.
- **Оставить** `pg-calcom` как есть.
- **Перенаправить DSN** всех app-сервисов с `pg-<svc>` на `postgres` (БД/логин/пароль остаются per-service). Конкретно:
  - event-saver `POSTGRES_DSN`, event-admin `POSTGRES_DSN` (оба `@postgres:5432/${PG_SAVER_DB}` кредами saver), event-users `POSTGRES_DSN`, event-notifier `DATABASE_URL`, event-shortener `POSTGRES_DSN`, event-db-sync `DATABASE_URL` → хост `postgres`.
  - event-booking `CALCOM_POSTGRES_DSN` и event-db-sync `CALCOM_DATABASE_URL` → `pg-calcom` без изменений.
- **`depends_on`** во всех app-сервисах: `pg-<svc>` → `postgres` (`condition: service_healthy`); у event-booking/event-db-sync дополнительно `pg-calcom`.
- **volumes** (top-level): убрать `pg-saver-data`/`pg-users-data`/`pg-notifier-data`/`pg-shortener-data`/`pg-db-sync-data`, добавить `pg-data`; `pg-calcom-data` оставить.

### 3. Exporters + Prometheus
- **Заменить** 5 экспортеров на 2:
  - `pg-exporter` (профиль observability): `DATA_SOURCE_NAME=postgresql://${PG_SUPERUSER:-postgres}:${PG_SUPERUSER_PASSWORD:-postgres}@postgres:5432/${PG_SUPERUSER_DB:-postgres}?sslmode=disable`, env `PG_EXPORTER_AUTO_DISCOVER_DATABASES=true`; `depends_on: postgres (service_healthy)`.
  - `pg-exporter-calcom` — без изменений.
- **`docker/prometheus/prometheus.yml`** job `postgres`: было 5 таргетов → 2 (`pg-exporter:9187` — метрики per-`datname` для всех app-БД; `pg-exporter-calcom:9187` label `db: calcom`).

### 4. `.env.example`
- Per-service `PG_*_USER` дефолты: `postgres` → роли `event_saver`/`event_users`/`event_notifier`/`event_shortener`/`event_db_sync` (чтобы реально различались логины). `PG_*_PASSWORD`/`PG_*_DB` оставить (значения dev-дефолтные).
- Добавить bootstrap-переменные общего инстанса: `PG_SUPERUSER=postgres`, `PG_SUPERUSER_PASSWORD=postgres`, `PG_SUPERUSER_DB=postgres`, опц. `PG_PORT=5432`.
- `PG_CALCOM_*` — без изменений. Удалить ставшие ненужными `PG_SHORTENER_PORT`/`PG_DB_SYNC_PORT` (их инстансы исчезли; общий порт — `PG_PORT`).
- Добавить ремарку: при переходе со старой схемы выполнить `docker compose down -v` (старые `pg-*-data` тома осиротеют; init нового общего инстанса отрабатывает только на чистом `pg-data`).

### 5. deploy (минимально)
- `deploy/scripts/seed-vault.sh`: поправить устаревший комментарий «4 app DBs on ONE instance» → «5 app DBs … (incl. event_db_sync)». Логику не трогать (уже на общем инстансе).
- Helm/ArgoCD/values — без изменений.

### 6. Документация
- `CLAUDE.md` (Quick Start / Host ports): обновить таблицу/упоминания pg-портов (был набор pg-*; стал один `postgres` на 5432 + `pg-calcom` на 5433). Отразить общий инстанс.
- README-ремарка про `down -v` при миграции схемы compose.

## Корректность, риски, миграция

- **Init только на чистом томе.** `docker-entrypoint-initdb.d` запускается лишь при первой инициализации `pg-data`. Существующие пользователи делают разовый `docker compose down -v`. Идемпотентные `IF NOT EXISTS` защищают от двойного прогона.
- **OWNER-роль** позволяет каждому сервису `alembic upgrade head` в своей БД. event-admin не мигрирует (read-only к event_saver).
- **Осиротевшие тома** `pg-saver-data` и т.п. остаются в Docker — упомянуть в README, что их можно удалить (`docker volume rm`).
- **scripts/calcom_sim.py** ходит в `pg-calcom` (5433) — не затрагивается.
- **Порядок старта**: один healthcheck общего инстанса; init создаёт все БД до того, как сервисы попытаются мигрировать (`depends_on: service_healthy`).

## Тестирование

- `docker compose config` — парсится без ошибок.
- `docker compose up -d postgres` → `psql -c "\l"` показывает 5 app-БД + `\du` показывает 5 ролей; healthcheck green.
- Поднять event-saver + event-users + event-db-sync → `alembic upgrade head` проходит под per-service ролью, сервисы стартуют (healthcheck green).
- (observability) `docker compose --profile observability up -d pg-exporter` → `curl pg-exporter:9187/metrics` отдаёт метрики с лейблом `datname` по всем app-БД, включая `event_db_sync`.
- Prometheus job `postgres` — 2 таргета UP.

## За рамками (YAGNI)

- Слияние cal.com в общий инстанс (в проде он отдельный — оставляем отдельным и в compose).
- Изменения prod Helm/values (уже на общем инстансе).
- Ротация/секрет-менеджмент паролей в dev (dev-дефолты остаются простыми).
