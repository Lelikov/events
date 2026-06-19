# cal.com как БД в общем Postgres + копия реальных данных — дизайн

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

Свернуть отдельный контейнер `pg-calcom` в **отдельную БД `calcom`** внутри общего
`postgres`-инстанса (со своей ролью/кредами), и в проде держать cal.com на **том же** managed-инстансе,
что и остальные app-БД (не отдельный инстанс). Затем разово скопировать **все данные** из внешней
реальной cal.com `postgresql://calendar:@127.0.0.1:5445/calendso` в эту БД.

## Решения (из брейнсторма)

1. Имя/креды: **`calcom` / `calcom` / `calcom`** (БД, роль, пароль) — минимум правок (PG_CALCOM_*/DSN уже такие).
2. Сид: **фикстура** (`docker/calcom-init/01-schema.sql`) остаётся дефолтом для bare-up/CI; реальные данные —
   перезапускаемым `scripts/copy_calcom.sh` (сами данные **не коммитятся** — privacy, 15 MB).
3. **Один общий инстанс везде** (compose + prod + kind): cal.com — 6-я БД на общем инстансе, отдельного
   cal.com-инстанса больше нет. Прод тоже правим (seed-vault: `10.16.0.41` → `10.16.0.40`).

## Контекст

- Внешняя cal.com `127.0.0.1:5445/calendso` (роль `calendar`, без пароля) доступна: **78 таблиц, 15 MB**,
  реальная схема. `pg_dump`/`psql` локально есть (homebrew).
- Compose сейчас: `pg-calcom` (init из `docker/calcom-init/01-schema.sql`, фикстура на ~3 таблицы),
  `pg-exporter-calcom`, том `pg-calcom-data`; DSN `@pg-calcom:5432/calcom` в event-booking
  (`CALCOM_POSTGRES_DSN`, строка 402) и event-db-sync (`CALCOM_DATABASE_URL`, plain, строка 593).
- `scripts/calcom_sim.py`: `DEFAULT_DSN=postgresql://calcom:calcom@localhost:5433/calcom`; мапит
  `@pg-calcom:5432` → `@localhost:{PG_CALCOM_PORT:-5433}` (строки 101-105).
- prod (`seed-vault.sh`): `CALCOM_DSN_PH`/`CALCOM_PLAIN_DSN_PH` → `@10.16.0.41` (отдельный); app-БД → `@10.16.0.40`.
- kind (`values-kind.yaml`): уже `CREATE DATABASE calcom;` на одном `devpostgresql` — **уже консистентно**.
- Общий `postgres` (предыдущая задача) уже хостит 5 app-БД + per-service роли через
  `docker/postgres-init/00-init-databases.sh`; exporter `pg-exporter` с `AUTO_DISCOVER_DATABASES=true`.

## Компоненты

### 1. Общий `postgres` init — добавить calcom + загрузить фикстуру в неё
- В сервис `postgres` (docker-compose) добавить env `PG_CALCOM_USER/PASSWORD/DB` (по умолчанию `calcom`) —
  их прочитает init-скрипт. Примонтировать `./docker/calcom-init:/calcom-init:ro` (НЕ в `initdb.d`, иначе
  фикстура выполнится против БД `postgres`).
- В `docker/postgres-init/00-init-databases.sh`: добавить `create_db_role "${PG_CALCOM_DB:-calcom}"
  "${PG_CALCOM_USER:-calcom}" "${PG_CALCOM_PASSWORD:-calcom}"`, затем загрузить фикстуру именно в БД `calcom`
  под ролью `calcom` (чтобы calcom владел таблицами и мог создавать триггеры event-db-sync):
  ```sh
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${PG_CALCOM_DB:-calcom}" \
    -c "SET ROLE \"${PG_CALCOM_USER:-calcom}\";" -f /calcom-init/01-schema.sql
  ```

### 2. Compose — убрать отдельный cal.com
- **Удалить** сервис `pg-calcom` (строки ~75-88), exporter `pg-exporter-calcom` (~130-136), том
  `pg-calcom-data` (~690).
- **DSN → хост `postgres`**: event-booking `CALCOM_POSTGRES_DSN` (402) и event-db-sync `CALCOM_DATABASE_URL`
  (593): `@pg-calcom:5432` → `@postgres:5432` (БД/креды `calcom` без изменений). `depends_on: pg-calcom` →
  `postgres` в обоих (строки ~426, ~603 — менять только этот ключ, остальные deps не трогать).
- **prometheus.yml**: убрать таргет `pg-exporter-calcom` (общий `pg-exporter` с auto-discover теперь покрывает
  и calcom — она в том же инстансе) → в job `postgres` остаётся 1 таргет `pg-exporter:9187` (label `db: app`).

### 3. `scripts/copy_calcom.sh` (новый, перезапускаемый; данные не коммитятся)
```sh
#!/bin/bash
set -euo pipefail
SRC="${CALCOM_SOURCE_DSN:-postgresql://calendar:@127.0.0.1:5445/calendso}"
echo "Wiping fixture + loading real cal.com from $SRC ..."
docker compose exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -d "${PG_CALCOM_DB:-calcom}" -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"${PG_CALCOM_USER:-calcom}\";"
pg_dump "$SRC" --no-owner --no-privileges \
  | docker compose exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -d "${PG_CALCOM_DB:-calcom}" -v ON_ERROR_STOP=1
docker compose exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -d "${PG_CALCOM_DB:-calcom}" \
  -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO \"${PG_CALCOM_USER:-calcom}\"; \
      GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO \"${PG_CALCOM_USER:-calcom}\"; \
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"${PG_CALCOM_USER:-calcom}\";"
echo "Done."
```
- `pg_dump` (homebrew) читает внешнюю БД на хосте, пайпит в compose-БД `calcom`. `--no-owner --no-privileges`
  снимает зависимость от роли `calendar`. Грузится под superuser (расширения cal.com), потом GRANT роли calcom.
- Запустить сейчас (разово), результат — ~78 таблиц с данными в `calcom`.

### 4. `scripts/calcom_sim.py` — repoint на общий инстанс
- `DEFAULT_DSN` → `postgresql://calcom:calcom@localhost:5432/calcom`; мапинг хоста (строки 101-105):
  заменять `@postgres:5432` → `@localhost:{PG_PORT:-5432}` (вместо `@pg-calcom:5432`/`PG_CALCOM_PORT`).
  Обновить docstring (упоминание `pg-calcom`, `5433`).

### 5. `.env.example`
- `PG_CALCOM_*` остаются (db/role `calcom`). Убрать `#PG_CALCOM_PORT=5433` (отдельного контейнера нет;
  общий порт — `PG_PORT=5432`). Заметку про `CALCOM_DATABASE_URL` override сохранить.

### 6. deploy — cal.com на общий инстанс
- `deploy/scripts/seed-vault.sh`: `CALCOM_DSN_PH` (112) и `CALCOM_PLAIN_DSN_PH` (114): `@10.16.0.41` →
  `@10.16.0.40`. Комментарий-топология (100-104): убрать строку «10.16.0.41 — cal.com (separate)», cal.com —
  6-я БД на `10.16.0.40` (своя роль `calcom`); «5 app DBs» → «5 app DBs + cal.com».
- `values-kind.yaml`: без изменений (уже `CREATE DATABASE calcom;` на одном инстансе).
- Helm/charts: без изменений (DSN приходят из Vault; топология инстанса абстрагирована env).

### 7. Документация
- `CLAUDE.md`: убрать упоминания отдельного `pg-calcom`/порта 5433 — cal.com теперь БД в общем `postgres`
  (порт 5432). Описать `scripts/copy_calcom.sh`.

## Корректность / риски

- **Владение схемой:** фикстура грузится под ролью `calcom` (SET ROLE) → calcom владеет таблицами и может
  создавать триггеры event-db-sync. Для реальной копии — GRANT ALL + ALTER DEFAULT PRIVILEGES роли calcom.
- **Реальная схема:** cal.com `users` имеет `updatedAt`, `Attendee` — нет (совпадает с уже сделанным фиксом
  reader event-db-sync). event-db-sync применит триггеры поверх реальной схемы на старте.
- **Разовая миграция:** `docker compose down -v` (тома пересоздаются); текущий стек запущен от smoke.
- **Расширения cal.com:** дамп грузится superuser'ом → `CREATE EXTENSION` проходит.
- **Реальные данные не коммитятся** (privacy/15 MB): только скрипт; фикстура — дефолт для CI/bare-up.

## Тестирование

- `docker compose config` валиден; `up postgres` → init создаёт `calcom` БД+роль+фикстуру; `\dt calcom`
  показывает фикстурные таблицы.
- `scripts/copy_calcom.sh` → `\dt` в calcom ≈ 78 таблиц; `SELECT count(*) FROM users` совпадает с источником (3).
- event-booking + event-db-sync стартуют на `@postgres/calcom`; event-db-sync применяет триггеры; smoke
  (insert Attendee → `user.upserted` → event-users upsert) проходит на реальной схеме.
- `scripts/calcom_sim.py create` пишет в `localhost:5432/calcom`.

## За рамками (YAGNI)

- Коммит реальных данных cal.com в репозиторий.
- Изменения Helm-чартов (топология инстанса — забота оператора/Vault DSN).
- Сохранение отдельного cal.com-инстанса где-либо.
