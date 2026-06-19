# Удаление мёртвой CRM-обвязки event-users — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Удалить мёртвый входящий CRM-поллер и исходящий CRM-вебхук из event-users (код, конфиг, миграцию-дроп `webhook_outbox`, тесты, env), сохранив event-db-sync-driven апсёрт и поток смены email.

**Architecture:** Чистка-удаление: модули `crm/` + `webhook/`, их провайдеры/конфиг/метрики/main-обвязка, 3 неиспользуемых метода changelog + ORM `webhook_outbox` + alembic-дроп; крест-репо — env в `.env.example`/`docker-compose.services.yml`/`seed-vault.sh`. Доказательство — зелёный pytest + чистый импорт + `alembic upgrade head`.

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy/Alembic, pytest. Репо: `event-users` + root `events`.

**Spec:** `docs/superpowers/specs/2026-06-19-remove-crm-machinery-design.md`

---

## Conventions

- Ветки: в `event-users` (репо на `main`) — `git checkout -b chore/remove-crm-machinery`; правки root (env) делаем в root-репо (на `main`, отдельный коммит).
- Коммиты заканчиваются:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **НЕ пушить.**
- TDD-наоборот (удаление): после каждого блока удалений — `uv run pytest -q` + `python -c "import event_users.main"` зелёные.
- **Оставить нетронутым:** `upsert_user_from_crm` (adapters/users_db.py), `email_source`, `user_email_changelog` + `add_entry`/`get_changelog`, поток `handle_email_change` (кроме вызова outbox).

---

### Task 1: Снять main/ioc-обвязку поллера и вебхука

**Files:**
- Modify: `event_users/main.py`, `event_users/ioc.py`

- [ ] **Step 1: Ветка**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-users
git checkout -b chore/remove-crm-machinery
```

- [ ] **Step 2: `main.py`** — удалить:
  - импорты `from event_users.crm.sync import CrmSyncRunner` и `from event_users.webhook.sender import WebhookOutboxSender`;
  - блок `sync_task`/`if settings.is_sync_enabled: … CrmSyncRunner …` (старт + лог про `crm_sync_interval_seconds`) и его shutdown-отмену;
  - блок `webhook_task`/`if settings.is_webhook_enabled: … WebhookOutboxSender …` и его shutdown-отмену.
  Оставить запуск email-consumer (`EmailChangeConsumer`) и прочую lifespan-логику.

- [ ] **Step 3: `ioc.py`** — удалить:
  - импорты `CrmClient`, `CrmSyncRunner`, `CrmWebhookClient`, `WebhookOutboxSender`;
  - провайдеры `provide_crm_client`, `provide_crm_sync_runner`, `provide_webhook_client`, `provide_webhook_sender`.
  Оставить остальные провайдеры (broker, sync_publisher, email-consumer, sessionmaker, sql, users-adapter, changelog, cache).

- [ ] **Step 4: Проверка импорта** (ещё упадёт на config-полях/модулях, удаляемых в следующих задачах — это нормально, если падение ТОЛЬКО про отсутствующие удаляемые сущности; если main.py теперь ссылается на несуществующее — поправить)
```bash
python -c "import event_users.main" 2>&1 | tail -5 || echo "(будет дочищено далее)"
```

- [ ] **Step 5: Commit**
```bash
git add event_users/main.py event_users/ioc.py
git commit -m "chore(event-users): unwire CRM poller + webhook from main/ioc

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Удалить модули crm/ и webhook/ + метрики + конфиг

**Files:**
- Delete: `event_users/crm/`, `event_users/webhook/`
- Modify: `event_users/config.py`, `event_users/metrics.py`

- [ ] **Step 1: Удалить модули**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-users
git rm -r event_users/crm event_users/webhook
```

- [ ] **Step 2: `config.py`** — удалить поля и валидатор: `is_sync_enabled`, `crm_api_url`, `crm_api_token`,
  `crm_encryption_key` (+ `@field_validator("crm_encryption_key")`), `crm_sync_interval_seconds`,
  `crm_sync_max_backoff_seconds`, `crm_webhook_url`, `crm_webhook_token`, `is_webhook_enabled`,
  `webhook_poll_interval_seconds`, `webhook_batch_size`, `webhook_visibility_timeout_seconds`. Оставить
  остальные поля (rabbit_url, is_consumer_enabled, db, rabbit_publish_timeout, log_level и т.п.).

- [ ] **Step 3: `metrics.py`** — удалить определения метрик `users_crm_sync_records_total` и
  `users_crm_sync_cycles_total` (и связанные с ними хелперы/импорты, если есть и больше не используются).

- [ ] **Step 4: Проверка импорта**
```bash
python -c "import event_users.config; import event_users.metrics" 2>&1 | tail -3 && echo "IMPORTS OK"
```
Expected: `IMPORTS OK` (config/metrics чисты). `import event_users.main` может ещё падать на changelog/consumer — дочистим в Task 3/4.

- [ ] **Step 5: Commit**
```bash
git add event_users/config.py event_users/metrics.py
git commit -m "chore(event-users): delete crm/ + webhook/ modules; drop CRM config + metrics

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Вычистить changelog-адаптер (3 метода) + consumer outbox-вызов

**Files:**
- Modify: `event_users/adapters/changelog_db.py`, `event_users/interfaces/changelog.py`, `event_users/consumer.py`
- Modify: `tests/adapters/*` (если тестируют удаляемые методы)

- [ ] **Step 1: `consumer.py`** — в `handle_email_change` удалить блок вызова
  `await changelog_db.add_webhook_outbox(event_type="user.email.changed", payload=…)` (запись в outbox).
  Остальное (idempotency `add_entry`, UPDATE users, contacts, cache invalidation, commit) — оставить.

- [ ] **Step 2: `adapters/changelog_db.py`** — удалить методы `get_admin_changed_email_roles`,
  `is_email_changed_by_admin`, `add_webhook_outbox`. Оставить `get_changelog`, `add_entry`,
  `_entry_from_row` и конструктор.

- [ ] **Step 3: `interfaces/changelog.py`** — удалить сигнатуры тех же 3 методов из Protocol.

- [ ] **Step 4: Проверка импорта всего приложения**
```bash
python -c "import event_users.main; import event_users.ioc; import event_users.consumer" 2>&1 | tail -5 && echo "APP IMPORTS OK"
```
Expected: `APP IMPORTS OK` (после Task 1-3 приложение импортируется без CRM/webhook). Если ссылка на
`webhook_outbox`-модель ещё держит импорт — она в `db/models.py`, удаляем в Task 4 (модель не импортируется
в рантайме main, только alembic) — но если что-то её тянет, отметь.

- [ ] **Step 5: Commit**
```bash
git add event_users/consumer.py event_users/adapters/changelog_db.py event_users/interfaces/changelog.py
git commit -m "chore(event-users): drop orphaned changelog methods + email-change outbox write

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Удалить ORM webhook_outbox + миграция-дроп

**Files:**
- Modify: `event_users/db/models.py`
- Create: `alembic/versions/0006_drop_webhook_outbox.py`

- [ ] **Step 1: `db/models.py`** — удалить ORM-класс таблицы `webhook_outbox` (включая его `__table_args__`/
  индексы `ix_webhook_outbox_pending` и т.п.). Оставить модели `users`, `user_contacts`,
  `user_email_changelog`.

- [ ] **Step 2: Миграция** — `alembic/versions/0006_drop_webhook_outbox.py`. Сверь точное имя индекса и
  колонки с тем, как `0004_email_source_changelog_webhook_outbox.py` создавал `webhook_outbox`, и зеркаль в
  downgrade:
```python
"""drop webhook_outbox (CRM webhook removed)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_webhook_outbox_pending", table_name="webhook_outbox")
    op.drop_table("webhook_outbox")


def downgrade() -> None:
    # mirror of 0004's webhook_outbox creation
    op.create_table(
        "webhook_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_webhook_outbox_pending", "webhook_outbox", ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
```
ВАЖНО: ОТКРОЙ `alembic/versions/0004_email_source_changelog_webhook_outbox.py` и приведи `downgrade` в
соответствие РЕАЛЬНОЙ структуре таблицы (имена/типы колонок, точное имя индекса и его `postgresql_where`).
Если структура отличается от показанной — используй реальную. `revision`/`down_revision` строки сверь со
стилем существующих миграций (там ревизии вида `"0005"` или `"0005_changelog_message_id"` — используй тот же
формат идентификатора, что и у `down_revision` цели).

- [ ] **Step 3: Проверка alembic (offline)**
```bash
uv run alembic history 2>&1 | tail -3
```
Expected: ревизия `0006` (drop webhook_outbox) видна как head, цепочка от `0005` без ошибок импорта.

- [ ] **Step 4: Commit**
```bash
git add event_users/db/models.py alembic/versions/0006_drop_webhook_outbox.py
git commit -m "chore(event-users): drop webhook_outbox table (model + migration)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Тесты + conftest + полный прогон

**Files:**
- Delete: `tests/crm/`, `tests/webhook/`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Удалить тест-директории**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-users
git rm -r tests/crm tests/webhook
```

- [ ] **Step 2: `tests/conftest.py`** — удалить `os.environ.setdefault` для `CRM_API_URL`, `CRM_API_TOKEN`,
  `CRM_ENCRYPTION_KEY`, `IS_SYNC_ENABLED`, `IS_WEBHOOK_ENABLED`. Прочие env-дефолты (rabbit, db) оставить.

- [ ] **Step 3: Найти и поправить осиротевшие тесты** — если в `tests/adapters/` есть тест changelog,
  проверяющий удалённые методы (`get_admin_changed_email_roles`/`is_email_changed_by_admin`/
  `add_webhook_outbox`) или в `tests/test_consumer.py` проверка `add_webhook_outbox` — удалить эти кейсы.
```bash
grep -rnE 'get_admin_changed_email_roles|is_email_changed_by_admin|add_webhook_outbox|webhook_outbox|CrmClient|CrmSync' tests/ | grep -v __pycache__ || echo "NO STALE TEST REFS"
```
Удалить найденные ссылки (кейсы/фейки), пока grep не даст `NO STALE TEST REFS`.

- [ ] **Step 4: Полный прогон + lint + импорт**
```bash
uv run ruff check --fix . && uv run ruff format .
uv run pytest -q 2>&1 | tail -3
python -c "import event_users.main; import event_users.ioc; import event_users.consumer; import event_users.config" && echo "APP OK"
```
Expected: pytest зелёный (без crm/webhook); `APP OK`. Если падает тест из-за оставшейся ссылки — дочистить.

- [ ] **Step 5: Commit**
```bash
git add tests/
git commit -m "chore(event-users): remove crm/webhook tests + CRM conftest env

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Документация event-users

**Files:**
- Modify: `event-users/docs/{SERVICE_OVERVIEW,DATA_MODEL,DEPENDENCIES,API_CONTRACTS,AUDIT}.md`, `event-users/CLAUDE.md`

- [ ] **Step 1:** Пройтись по docs и убрать описания удалённого: CRM-синк (поллер/расшифровка/admin-guard),
  исходящий вебхук/`webhook_outbox`, обязательные `crm_*` env. Где уместно — заменить упоминание «CRM sync»
  на «синхронизация через event-db-sync». `DATA_MODEL.md` — убрать абзац про `email_source`-гвард
  (`get_admin_changed_email_roles`); отметить, что `email_source` информационный. `DEPENDENCIES.md` — убрать
  внешний CRM как зависимость. `CLAUDE.md` — поправить, если упоминает sync/webhook.

- [ ] **Step 2:** Проверка, что не осталось ссылок на удалённый код
```bash
grep -rniE 'CrmClient|CrmSync|crm\.sync|crm/|is_sync_enabled|is_webhook_enabled|get_admin_changed' docs/ CLAUDE.md || echo "DOCS CLEAN"
```
Expected: `DOCS CLEAN` (или только исторические упоминания, которые осознанно оставлены — но лучше убрать).

- [ ] **Step 3: Commit**
```bash
git add docs/ CLAUDE.md
git commit -m "docs(event-users): drop CRM sync + webhook from service docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Env-чистка в root (compose/.env.example/seed-vault)

**Files (root repo `events`, ветка `main`):**
- Modify: `.env.example`, `docker-compose.services.yml`, `deploy/scripts/seed-vault.sh`

- [ ] **Step 1: `.env.example`** — удалить строки/комментарии секции CRM: `CRM_API_URL`, `CRM_API_TOKEN`,
  `CRM_ENCRYPTION_KEY`, `IS_SYNC_ENABLED`, `IS_WEBHOOK_ENABLED`, любые `CRM_WEBHOOK_*`.

- [ ] **Step 2: `docker-compose.services.yml`** — в сервисе `event-users` удалить env-ключи
  `IS_SYNC_ENABLED`, `CRM_API_URL`, `CRM_API_TOKEN`, `CRM_ENCRYPTION_KEY`, `IS_WEBHOOK_ENABLED`,
  `CRM_WEBHOOK_*` (если есть). Прочие env (RABBIT_URL, POSTGRES_DSN, EVENTS_*, OTEL_* и т.п.) — оставить.

- [ ] **Step 3: `deploy/scripts/seed-vault.sh`** — в блоке `put event-users …` удалить
  `IS_SYNC_ENABLED`, `CRM_API_URL` и прочие CRM/webhook-ключи. Если в блоке остаются плейсхолдеры
  `${CRM_*}`, ставшие ненужными вверху скрипта — убрать и их.

- [ ] **Step 4: Валидация**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
grep -nE 'CRM_|IS_SYNC|IS_WEBHOOK|CRM_WEBHOOK' .env.example docker-compose.services.yml deploy/scripts/seed-vault.sh || echo "ENV CLEAN"
docker compose config -q 2>&1 && echo "COMPOSE OK"
bash -n deploy/scripts/seed-vault.sh && echo "SEED SH OK"
```
Expected: `ENV CLEAN`, `COMPOSE OK`, `SEED SH OK`.

- [ ] **Step 5: Commit (root repo)**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add .env.example docker-compose.services.yml deploy/scripts/seed-vault.sh
git commit -m "chore: drop event-users CRM env (poller + webhook removed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Live smoke (CONTROLLER, Docker)

> Проверяем, что event-users стартует без CRM-env и поток смены email цел.

- [ ] **Step 1: Пересобрать + поднять event-users**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d --build event-users 2>&1 | tail -3
docker compose logs event-users 2>&1 | grep -iE 'alembic|upgrade|started|error|0006|webhook_outbox' | tail -6
docker compose ps event-users --format '{{.Status}}'
```
Expected: миграция `0006` применилась (drop webhook_outbox), сервис healthy, без ошибок про отсутствующий CRM-env.

- [ ] **Step 2: webhook_outbox дропнута, остальное на месте**
```bash
docker compose exec -T postgres psql -U postgres -d event_users -tAc "SELECT to_regclass('public.webhook_outbox') IS NULL AS dropped;"
docker compose exec -T postgres psql -U postgres -d event_users -tAc "SELECT count(*) AS users FROM users; SELECT count(*) AS changelog FROM user_email_changelog;"
```
Expected: `dropped = t`; users/changelog читаются.

- [ ] **Step 3:** Результат — контроллеру (smoke passed).

---

## Self-review

- **Spec coverage:** main/ioc unwire (T1), удаление crm/+webhook/+config+metrics (T2), changelog-методы +
  consumer outbox (T3), ORM+миграция-дроп (T4), тесты+conftest (T5), docs (T6), root env (T7), smoke (T8). ✅
- **Keep-инвариант:** `upsert_user_from_crm`, `email_source`, `user_email_changelog`/`add_entry`/`get_changelog`,
  поток `handle_email_change` (минус outbox) — нигде не удаляются. ✅
- **Placeholder scan:** конкретные команды/диффы; миграция требует сверки с 0004 (явно указано). ✅
- **Порядок:** импорт-чистота достигается к концу T3; pytest-зелёный — к концу T5; миграция применяется в
  smoke (T8). ✅
- **Кросс-репо:** event-users (T1-T6, ветка chore/remove-crm-machinery) + root env (T7, main). ✅
