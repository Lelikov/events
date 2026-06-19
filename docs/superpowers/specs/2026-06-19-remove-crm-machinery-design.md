# Удаление мёртвой CRM-обвязки event-users — дизайн

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

Удалить из `event-users` всю CRM-обвязку, ставшую мёртвой после ввода `event-db-sync`:
- **входящий поллер** (`CrmClient` + `CrmSyncService`/`CrmSyncRunner`) — тянул юзеров ИЗ CRM по HTTP
  (заменён триггер-синком cal.com → `user.upserted`);
- **исходящий вебхук** (`WebhookOutboxSender` + `CrmWebhookClient`, `webhook_outbox`) — пушил изменения email
  ОБРАТНО в CRM.

Оба выключены по умолчанию (`is_sync_enabled=False`, `is_webhook_enabled=False`) и не нужны. Заодно
снимается заусенец: `crm_api_url`/`crm_api_token`/`crm_encryption_key` были `Field(strict=True)` —
обязательный конфиг ради мёртвой фичи.

## Что ОСТАЁТСЯ (не трогаем)

- `upsert_user_from_crm` в `adapters/users_db.py` — его вызывает консьюмер `handle_user_upserted` (поток
  event-db-sync). Имя историческое; переименование — вне scope.
- Колонка `users.email_source` — выставляется (`handle_email_change`→`'admin'`, `upsert_user_from_crm`→`'crm'`),
  но больше не «гвардится». Оставляем как информационную (удаление — отдельный вопрос, не сейчас).
- Таблица `user_email_changelog` + методы `add_entry`/`get_changelog` — идемпотентность смены email
  (`consumer.handle_email_change`, `controllers/users`, `routes`) и история. Остаются.
- Весь поток смены email (`user.email.change_requested` → `handle_email_change` → UPDATE users + contacts).

## Удаляемые компоненты (event-users)

### Код
- `event_users/crm/` целиком (`client.py`, `sync.py`, `__init__.py`).
- `event_users/webhook/` целиком (`client.py`, `sender.py`, `__init__.py`).
- `config.py`: удалить поля `is_sync_enabled`, `crm_api_url`, `crm_api_token`, `crm_encryption_key`
  (+ его `field_validator`), `crm_sync_interval_seconds`, `crm_sync_max_backoff_seconds`, `crm_webhook_url`,
  `crm_webhook_token`, `is_webhook_enabled`, `webhook_poll_interval_seconds`, `webhook_batch_size`,
  `webhook_visibility_timeout_seconds`.
- `ioc.py`: удалить провайдеры `provide_crm_client`, `provide_crm_sync_runner`, `provide_webhook_client`,
  `provide_webhook_sender` + соответствующие импорты.
- `main.py`: удалить блоки запуска/останова `CrmSyncRunner` (`if is_sync_enabled`) и `WebhookOutboxSender`
  (`if is_webhook_enabled`) + импорты + лог про `crm_sync_interval_seconds`.
- `metrics.py`: удалить `users_crm_sync_records_total`, `users_crm_sync_cycles_total` (других потребителей нет).
- `consumer.py` `handle_email_change`: удалить вызов `changelog_db.add_webhook_outbox(...)` (запись в outbox
  нужна была только исходящему вебхуку).
- `adapters/changelog_db.py`: удалить методы `get_admin_changed_email_roles`, `is_email_changed_by_admin`
  (нигде не вызываются после удаления поллера), `add_webhook_outbox` (только вебхук).
- `interfaces/changelog.py`: удалить те же 3 сигнатуры из Protocol.
- `db/models.py`: удалить ORM-модель таблицы `webhook_outbox` (класс + индексы). `user_email_changelog`
  оставить.

### Миграция
- Новая `alembic/versions/0006_drop_webhook_outbox.py` (`down_revision = '0005'`): `op.drop_index(...)` +
  `op.drop_table('webhook_outbox')`; `downgrade` воссоздаёт таблицу (зеркало части `0004`).

### Тесты
- Удалить `tests/crm/` и `tests/webhook/` целиком.
- `tests/conftest.py`: удалить `os.environ.setdefault` для `CRM_API_URL`, `CRM_API_TOKEN`,
  `CRM_ENCRYPTION_KEY`, `IS_SYNC_ENABLED`, `IS_WEBHOOK_ENABLED`.
- Прогнать оставшийся набор — должен быть зелёным (idempotency-тесты changelog, consumer, adapters, routes).

### Документация (event-users/docs)
- `SERVICE_OVERVIEW.md`, `DATA_MODEL.md`, `DEPENDENCIES.md`, `API_CONTRACTS.md`, `AUDIT.md`: убрать описания
  CRM-синка/админ-гварда/исходящего вебхука/`webhook_outbox`; где уместно — отметить, что синк теперь
  через `event-db-sync`.
- `CLAUDE.md` event-users (если упоминает CRM sync/webhook) — поправить.

## Удаляемый env (root)

- `.env.example`: удалить `CRM_API_URL`, `CRM_API_TOKEN`, `CRM_ENCRYPTION_KEY`, `IS_SYNC_ENABLED`,
  `IS_WEBHOOK_ENABLED`, `CRM_WEBHOOK_*` (если есть) + комментарии секции CRM.
- `docker-compose.services.yml`: в сервисе `event-users` удалить env `IS_SYNC_ENABLED`, `CRM_API_URL`,
  `CRM_API_TOKEN`, `CRM_ENCRYPTION_KEY`, `IS_WEBHOOK_ENABLED`, `CRM_WEBHOOK_*` (если есть).
- `deploy/scripts/seed-vault.sh`: в `put event-users` удалить `IS_SYNC_ENABLED`, `CRM_API_URL` и прочие
  CRM/webhook-ключи.
- `docker/mocks/mappings/`: если есть мок-маппинг под `/crm` (использовался поллером) — можно удалить
  (опционально; проверить, не используется ли ещё чем-то).

## Корректность / риски

- **email-change поток не ломается**: `handle_email_change` после удаления `add_webhook_outbox` продолжает
  делать UPDATE users + idempotency через `add_entry`. Outbox был побочным.
- **changelog адаптер** остаётся (общий), удаляются только 3 неиспользуемых метода.
- **Конфиг становится мягче**: убираются обязательные `crm_*` strict-поля → сервис стартует без CRM-env.
- **Миграция**: `event-users` владеет своей БД — дроп `webhook_outbox` легитимен. На существующих стендах
  `alembic upgrade head` дропнет таблицу (пустую, т.к. вебхук был выключен).
- **Импорт-чистота**: после удаления проверить `python -c "import event_users.main"` + полный pytest.

## Тестирование

- `uv run pytest -q` (event-users) — зелёный без `tests/crm`/`tests/webhook`.
- `python -c "import event_users.main; import event_users.ioc; import event_users.consumer"` — без ошибок.
- `uv run alembic upgrade head` на чистой БД проходит; `webhook_outbox` отсутствует, `users`/`user_contacts`/
  `user_email_changelog` на месте.
- `docker compose config -q` (root) валиден после правки env.
- Live smoke (опц.): event-users поднимается без CRM-env, email-change поток работает.

## За рамками (YAGNI)

- Удаление колонки `email_source` и переименование `upsert_user_from_crm`.
- Изменение входящего синка (event-db-sync) и потока смены email.
- Трогать таблицу `user_email_changelog` (идемпотентность остаётся нужна).
