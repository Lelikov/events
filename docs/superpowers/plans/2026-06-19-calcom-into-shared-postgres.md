# cal.com в общий Postgres + копия реальных данных — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Свернуть отдельный `pg-calcom` в БД `calcom` внутри общего `postgres`-инстанса (один инстанс везде — compose/prod/kind), и разово скопировать данные из внешней реальной cal.com.

**Architecture:** `00-init-databases.sh` создаёт БД `calcom` + роль `calcom` и грузит фикстуру в неё (под ролью calcom); `pg-calcom`/`pg-exporter-calcom`/том удаляются; DSN cal.com переезжают на хост `postgres`; новый `scripts/copy_calcom.sh` дампит внешнюю cal.com в compose-БД (перезапускаемо); prod seed-vault переводит cal.com на общий `10.16.0.40`.

**Tech Stack:** Docker Compose, postgres:16, pg_dump/psql (homebrew), prometheus.

**Spec:** `docs/superpowers/specs/2026-06-19-calcom-into-shared-postgres-design.md`

---

## Conventions

- Один репозиторий — root `events`. Перед стартом создать ветку (репо на `main`):
  ```bash
  cd /Users/alexandrlelikov/PycharmProjects/events && git checkout -b feat/calcom-shared-postgres
  ```
- Сообщения коммитов заканчиваются:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **НЕ пушить** (merge/push — отдельный user-gated шаг).
- Инфра-конфиг: «тест» = `docker compose config -q`; fallback `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"`.
- Промежуточные состояния compose могут быть временно невалидны (висящие ссылки на удаляемый `pg-calcom`) — полная валидация в конце Task 2.

## Канонические значения

| Что | Значение |
|---|---|
| cal.com БД / роль / пароль | `calcom` / `calcom` / `calcom` |
| хост cal.com (compose) | `postgres` (общий инстанс) |
| внешний источник | `postgresql://calendar:@127.0.0.1:5445/calendso` |
| prod cal.com хост | `10.16.0.40` (общий managed, было `10.16.0.41`) |
| фикстура | `docker/calcom-init/01-schema.sql` (монтируется как `/calcom-init`) |

---

### Task 1: init общего postgres создаёт calcom-БД + грузит фикстуру

**Files:**
- Modify: `docker/postgres-init/00-init-databases.sh`
- Modify: `docker-compose.yml` (сервис `postgres`: env `PG_CALCOM_*` + mount `/calcom-init`)

- [ ] **Step 1: Init-скрипт** — в `docker/postgres-init/00-init-databases.sh` после строки
  `create_db_role "${PG_DB_SYNC_DB:-event_db_sync}" ...` добавить создание calcom + загрузку фикстуры:
```sh
create_db_role "${PG_CALCOM_DB:-calcom}"             "${PG_CALCOM_USER:-calcom}"             "${PG_CALCOM_PASSWORD:-calcom}"

# cal.com lives on the shared instance too. Load the dev fixture INTO the calcom DB
# under the calcom role (so it owns the tables and event-db-sync can create its triggers).
# Real data is loaded separately via scripts/copy_calcom.sh.
if [ -f /calcom-init/01-schema.sql ]; then
  echo "  loading cal.com fixture schema into ${PG_CALCOM_DB:-calcom}..."
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${PG_CALCOM_DB:-calcom}" \
    -c "SET ROLE \"${PG_CALCOM_USER:-calcom}\";" -f /calcom-init/01-schema.sql
fi
```
(Поставить ПЕРЕД финальным `echo "Done."`.)

- [ ] **Step 2: Compose — env + mount** — в сервисе `postgres` (`docker-compose.yml`) добавить в `environment:`
  (рядом с другими PG_*):
```yaml
      PG_CALCOM_USER: ${PG_CALCOM_USER:-calcom}
      PG_CALCOM_PASSWORD: ${PG_CALCOM_PASSWORD:-calcom}
      PG_CALCOM_DB: ${PG_CALCOM_DB:-calcom}
```
и в `volumes:` сервиса `postgres` добавить mount фикстуры (НЕ в initdb.d):
```yaml
      - ./docker/calcom-init:/calcom-init:ro
```

- [ ] **Step 3: Валидация синтаксиса**
```bash
bash -n docker/postgres-init/00-init-databases.sh && echo "SH OK"
docker compose config -q 2>&1 && echo "COMPOSE OK" || python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('YAML OK')"
```
Expected: `SH OK`, `COMPOSE OK`/`YAML OK`. (Ссылки на `pg-calcom` ещё есть в других сервисах — это чинит Task 2; если `docker compose config` падает на висящем `pg-calcom`, отметь и продолжай.)

- [ ] **Step 4: Commit**
```bash
git add docker/postgres-init/00-init-databases.sh docker-compose.yml
git commit -m "feat(compose): shared postgres provisions calcom DB + loads fixture into it

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: убрать pg-calcom, перенаправить DSN, exporter

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker/prometheus/prometheus.yml`

- [ ] **Step 1: Удалить контейнер cal.com** — удалить из `docker-compose.yml` блок сервиса `pg-calcom`
  (вместе с комментарием-заголовком над ним) и блок exporter `pg-exporter-calcom`. В top-level `volumes:`
  удалить `pg-calcom-data:`.

- [ ] **Step 2: DSN → хост postgres** — заменить хост в двух местах:
  - event-booking `CALCOM_POSTGRES_DSN`: `...@pg-calcom:5432/calcom}` → `...@postgres:5432/calcom}`
    (строка вида `CALCOM_POSTGRES_DSN: ${CALCOM_DATABASE_URL:-postgresql+asyncpg://calcom:calcom@pg-calcom:5432/calcom}`
    → `...@postgres:5432/calcom}`).
  - event-db-sync `CALCOM_DATABASE_URL`: `...@pg-calcom:5432/${PG_CALCOM_DB:-calcom}` →
    `...@postgres:5432/${PG_CALCOM_DB:-calcom}`.

- [ ] **Step 3: depends_on** — в event-booking и event-db-sync заменить ключ зависимости `pg-calcom:` →
  `postgres:` (`condition: service_healthy` сохранить). Если в сервисе уже есть `postgres:` (event-db-sync
  зависит и от postgres, и от pg-calcom) — убрать дублирующий `pg-calcom:` блок, оставив один `postgres:`.
  Прочие зависимости (rabbitmq и т.п.) не трогать.

- [ ] **Step 4: prometheus** — в `docker/prometheus/prometheus.yml`, job `postgres`, удалить таргет
  `pg-exporter-calcom` (оставить только `pg-exporter:9187` с label `db: app`). Итог:
```yaml
  - job_name: postgres
    static_configs:
      - targets: ["pg-exporter:9187"]
        labels: { db: app }
```

- [ ] **Step 5: Полная валидация**
```bash
docker compose config -q 2>&1 && echo "COMPOSE OK" || python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('YAML OK')"
python3 -c "import yaml; yaml.safe_load(open('docker/prometheus/prometheus.yml')); print('PROM OK')"
echo "--- ссылок на pg-calcom быть не должно (ждём NONE) ---"
grep -nE 'pg-calcom' docker-compose.yml docker/prometheus/prometheus.yml || echo "NONE"
echo "--- calcom DSN теперь на @postgres ---"
grep -nE 'CALCOM_POSTGRES_DSN|CALCOM_DATABASE_URL' docker-compose.yml
```
Expected: `COMPOSE OK`, `PROM OK`, grep `pg-calcom` → `NONE`, оба calcom DSN на `@postgres`.

- [ ] **Step 6: Commit**
```bash
git add docker-compose.yml docker/prometheus/prometheus.yml
git commit -m "feat(compose): drop pg-calcom container; cal.com is a DB on shared postgres

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: scripts/copy_calcom.sh (копия реальных данных)

**Files:**
- Create: `scripts/copy_calcom.sh`

- [ ] **Step 1: Создать скрипт** `scripts/copy_calcom.sh`:
```sh
#!/bin/bash
# One-time / re-runnable copy of a real cal.com DB into the dev compose `calcom`
# database (shared postgres instance). Reads the source with the host's pg_dump,
# pipes the dump into the compose postgres container. Real data is NOT committed.
#
# Usage:  scripts/copy_calcom.sh
#         CALCOM_SOURCE_DSN=postgresql://user:pass@host:port/db scripts/copy_calcom.sh
set -euo pipefail

SRC="${CALCOM_SOURCE_DSN:-postgresql://calendar:@127.0.0.1:5445/calendso}"
SUPER="${PG_SUPERUSER:-postgres}"
CAL_DB="${PG_CALCOM_DB:-calcom}"
CAL_USER="${PG_CALCOM_USER:-calcom}"

echo ">> Wiping fixture schema in '${CAL_DB}' and loading real cal.com from ${SRC} ..."
docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"$CAL_USER\";"

pg_dump "$SRC" --no-owner --no-privileges \
  | docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 >/dev/null

docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 \
  -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO \"$CAL_USER\";
      GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO \"$CAL_USER\";
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"$CAL_USER\";"

tables=$(docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo ">> Done. '${CAL_DB}' now has ${tables} tables."
```

- [ ] **Step 2: chmod + синтаксис**
```bash
chmod +x scripts/copy_calcom.sh
bash -n scripts/copy_calcom.sh && echo "SH OK"
```
Expected: `SH OK`.

- [ ] **Step 3: Commit**
```bash
git add scripts/copy_calcom.sh
git commit -m "feat(scripts): copy_calcom.sh — load a real cal.com DB into dev compose calcom

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: repoint scripts/calcom_sim.py на общий инстанс

**Files:**
- Modify: `scripts/calcom_sim.py`

- [ ] **Step 1: Прочитать** `scripts/calcom_sim.py` строки ~39-105 (DEFAULT_DSN + функция-мапинг хоста на
  опубликованный порт). Сейчас: `DEFAULT_DSN = "postgresql://calcom:calcom@localhost:5433/calcom"`; мапинг
  заменяет `@pg-calcom:5432` → `@localhost:{PG_CALCOM_PORT:-5433}`.

- [ ] **Step 2: Repoint** — внести правки:
  - `DEFAULT_DSN = "postgresql://calcom:calcom@localhost:5432/calcom"`.
  - В функции-мапинге (≈строки 101-105): использовать общий порт `PG_PORT` (default `5432`) и заменять
    хост `@postgres:5432` → `@localhost:{port}` (вместо `@pg-calcom:5432`/`PG_CALCOM_PORT`). Конкретно:
    ```python
    host_port = env.get("PG_PORT", "5432")
    dsn = env.get("CALCOM_DATABASE_URL", DEFAULT_DSN.replace(":5432/", f":{host_port}/"))
    ...
    return dsn.replace("@postgres:5432", f"@localhost:{host_port}")
    ```
  - Обновить docstring/комментарии, упоминающие `pg-calcom` / `5433` → общий `postgres` / `5432`.

- [ ] **Step 3: Синтаксис**
```bash
python3 -c "import ast; ast.parse(open('scripts/calcom_sim.py').read()); print('PY OK')"
grep -nE '5433|pg-calcom|PG_CALCOM_PORT' scripts/calcom_sim.py || echo "NO STALE REFS"
```
Expected: `PY OK`; grep — `NO STALE REFS` (или только в неактуальных комментариях, которые тоже стоит убрать).

- [ ] **Step 4: Commit**
```bash
git add scripts/calcom_sim.py
git commit -m "fix(scripts): calcom_sim points at shared postgres (localhost:5432/calcom)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: .env.example + seed-vault.sh + CLAUDE.md

**Files:**
- Modify: `.env.example`
- Modify: `deploy/scripts/seed-vault.sh`
- Modify: `CLAUDE.md`

- [ ] **Step 1: .env.example** — удалить закомментированную строку `#PG_CALCOM_PORT=5433` (отдельного
  контейнера нет; общий порт `PG_PORT=5432`). `PG_CALCOM_USER/PASSWORD/DB=calcom` оставить. Поправить
  комментарий секции cal.com: «separate instance (own container)» → «database on the shared postgres
  instance». Заметку про `CALCOM_DATABASE_URL` override сохранить.

- [ ] **Step 2: seed-vault.sh** — в `deploy/scripts/seed-vault.sh`:
  - `CALCOM_DSN_PH`: `@10.16.0.41:5432/calcom` → `@10.16.0.40:5432/calcom`.
  - `CALCOM_PLAIN_DSN_PH`: `@10.16.0.41:5432/calcom` → `@10.16.0.40:5432/calcom`.
  - Комментарий-топология: удалить строку `#   10.16.0.41     PostgreSQL — cal.com (separate; only if
    self-hosted)`; в строке про `10.16.0.40` отразить, что cal.com — тоже БД на этом инстансе (роль `calcom`);
    «All five app DBs live on the SAME managed instance» → «All app DBs incl. cal.com live on the SAME
    managed instance (10.16.0.40)».

- [ ] **Step 3: CLAUDE.md** — убрать упоминания отдельного `pg-calcom`/порта `5433` (cal.com теперь БД в
  общем `postgres` на `5432`); в таблице host-портов строку про cal.com убрать/слить в строку
  `5432 | postgres (общий: app-БД + calcom)`. Описать `scripts/copy_calcom.sh` рядом с упоминанием
  `scripts/calcom_sim.py`. Точечные правки, без переписывания файла.

- [ ] **Step 4: Валидация**
```bash
grep -nE '10.16.0.41' deploy/scripts/seed-vault.sh && echo "STILL HAS .41 (BAD)" || echo "NO .41"
grep -nE 'CALCOM_(DSN|PLAIN_DSN)_PH.*10.16.0.40' deploy/scripts/seed-vault.sh
grep -nE 'PG_CALCOM_PORT|5433' .env.example || echo "ENV CLEAN"
bash -n deploy/scripts/seed-vault.sh && echo "SEED SH OK"
```
Expected: `NO .41`, обе calcom DSN на `10.16.0.40`, `ENV CLEAN`, `SEED SH OK`.

- [ ] **Step 5: Commit**
```bash
git add .env.example deploy/scripts/seed-vault.sh CLAUDE.md
git commit -m "docs: cal.com is a DB on the shared instance (compose + prod seed-vault)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Live smoke — пересоздать стек, скопировать реальные данные, e2e (CONTROLLER, Docker)

> Требует Docker + доступ к внешней cal.com `127.0.0.1:5445`. Разрушает текущие dev-тома (`down -v`).

- [ ] **Step 1: Чистый старт общего инстанса**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose down -v 2>/dev/null || true
docker compose up -d postgres
# дождаться healthy, проверить что calcom-БД создана с фикстурой
docker compose exec -T postgres psql -U postgres -tAc "SELECT datname FROM pg_database WHERE datname='calcom';"
docker compose exec -T postgres psql -U postgres -d calcom -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
```
Expected: `calcom` существует; фикстурные таблицы (>0, ~3).

- [ ] **Step 2: Копия реальных данных**
```bash
scripts/copy_calcom.sh
```
Expected: `Done. 'calcom' now has ~78 tables.` (без ошибок pg_dump/psql). Проверка:
```bash
docker compose exec -T postgres psql -U postgres -d calcom -tAc "SELECT 'users='||count(*) FROM users;"
```
Expected: совпадает с источником (≈`users=3`).

- [ ] **Step 3: Поднять сервисы на реальной calcom + e2e**
```bash
docker compose up -d --build pg=skip rabbitmq event-saver event-users event-db-sync event-booking 2>/dev/null || \
  docker compose up -d --build rabbitmq event-saver event-users event-db-sync
# event-db-sync применяет триггеры на реальной calcom-схеме; reconcile проходит
docker compose logs event-db-sync 2>&1 | grep -iE 'reconcile emitted|error|attribute|UndefinedColumn' | tail -5
docker compose exec -T postgres psql -U postgres -d calcom -tAc "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'user_sync%';"
```
Expected: триггеры `user_sync_attendee`/`user_sync_users` созданы; в логах нет `UndefinedColumn`/`attribute` ошибок; есть `Reconcile emitted` (по реальным строкам). При желании — повторить e2e (insert Attendee → user.upserted → event-users), как в предыдущем smoke.

- [ ] **Step 4: calcom_sim проверка (опц.)**
```bash
uv run scripts/calcom_sim.py create --dry-run 2>&1 | tail -3 || true
```
Expected: использует `localhost:5432/calcom` (или dry-run печатает корректный DSN/без подключения к 5433).

- [ ] **Step 5: Записать результат** контроллеру (smoke passed); коммитить нечего (данные не коммитятся).

---

## Self-review

- **Spec coverage:** init создаёт calcom+фикстуру (T1), удаление pg-calcom/exporter/том + DSN→postgres +
  prometheus (T2), copy_calcom.sh (T3) + запуск в smoke (T6), calcom_sim repoint (T4), .env/seed-vault/CLAUDE
  (T5). Прод на общий 10.16.0.40 (T5). ✅
- **Placeholder scan:** конкретный код/команды во всех шагах; smoke (T6) — отдельная controller-задача с Docker. ✅
- **Type consistency:** `calcom`/`calcom`/`calcom`, хост `postgres`, `@postgres:5432/calcom`, `10.16.0.40`,
  `PG_PORT` — одинаково в T1-T6. copy_calcom.sh переменные (`PG_SUPERUSER`/`PG_CALCOM_*`) совпадают с compose. ✅
- **Порядок валидации:** T1 оставляет висящие ссылки на pg-calcom; T2 их удаляет → полностью зелёный
  `docker compose config` к концу T2. Отмечено. ✅
- **Прод:** kind уже консистентен (не трогаем); seed-vault на 10.16.0.40; Helm не трогаем (DSN из Vault). ✅
