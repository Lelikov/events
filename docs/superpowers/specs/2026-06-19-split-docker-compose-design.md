# Разбиение docker-compose.yml через include — дизайн

**Дата:** 2026-06-19
**Статус:** утверждён к планированию

## Цель

Разбить большой `docker-compose.yml` (663 строки) на несколько файлов по слоям (infra / app-сервисы /
observability), чтобы каждый файл был обозримым. Поведение не меняется: `docker compose up -d` работает
без доп-флагов; `--profile observability` по-прежнему поднимает наблюдаемость.

## Решения (из брейнсторма)

1. Механизм — **`include:`** (Compose v5.1.2 поддерживает, нужен был ≥2.20).
2. Файлы — **в корне репо** (`docker-compose.infra.yml` и т.д.), чтобы относительные пути
   (`build.context: ./event-X`, `./docker/...`) резолвились как раньше, без `project_directory`/`../`.
3. App-сервисы — **в одном файле** `docker-compose.services.yml` (3-файловая схема).

## Контекст (текущая структура)

- `docker-compose.yml`: 663 строки, один `services:` + `volumes:`. Якорей (`&`/`*`/`<<`), `x-`-полей,
  явных `networks:` НЕТ; сеть — дефолтная `events_default`. Профиль `observability` у obs-сервисов.
- Сервисы (порядок в файле): rabbitmq, postgres, mocks, pg-exporter, prometheus, grafana, alertmanager,
  victorialogs, vector, tempo, otel-collector, vault, event-receiver, event-saver, event-booking,
  event-users, event-admin, event-notifier, event-db-sync, event-shortener, event-admin-frontend,
  jitsi-chat.
- Тома: `pg-data` (у postgres), `prometheus-data`, `alertmanager-data`, `victorialogs-data`, `tempo-data`
  (у obs-сервисов). App-сервисы именованных томов не используют.

## Целевая структура

- **`docker-compose.yml`** → тонкий:
  ```yaml
  include:
    - docker-compose.infra.yml
    - docker-compose.services.yml
    - docker-compose.observability.yml
  ```
- **`docker-compose.infra.yml`** — `services:` {rabbitmq, postgres, mocks, vault} + `volumes:` {pg-data}.
- **`docker-compose.services.yml`** — `services:` {event-receiver, event-saver, event-booking, event-users,
  event-admin, event-notifier, event-db-sync, event-shortener, event-admin-frontend, jitsi-chat}. Томов нет.
- **`docker-compose.observability.yml`** — `services:` {pg-exporter, prometheus, grafana, alertmanager,
  victorialogs, vector, tempo, otel-collector} + `volumes:` {prometheus-data, alertmanager-data,
  victorialogs-data, tempo-data}. У сервисов сохраняются `profiles: ["observability"]`.

## Корректность

- Блоки сервисов переносятся **дословно** (включая отступы и комментарии-заголовки). Пути не меняются.
- **Тома самодостаточны по файлам**: pg-data только в infra (у postgres), obs-тома в observability, в
  services томов нет — кросс-файловых ссылок на named volumes нет.
- **Кросс-файловые `depends_on`** (app → postgres/rabbitmq/mocks; pg-exporter → postgres): `include`
  сливает все файлы в одну модель проекта ДО валидации/резолва зависимостей — ссылки разрешаются.
- Сеть дефолтная (общая для проекта); якорей/`x-` нет — общего состояния между файлами не теряется.
- Разбитые файлы **не авто-загружаются** Compose (только `docker-compose.yml`/`docker-compose.override.yml`
  авто-грузятся) — они подключаются исключительно через `include`. `docker-compose.override.yml` (если
  появится) продолжит работать поверх.

## Тестирование

- **Эквивалентность:** `docker compose config` ДО (на исходном файле) и ПОСЛЕ (на сплите) дают
  **идентичный** нормализованный merged-вывод — это доказательство, что сплит ничего не изменил.
- `docker compose config -q` — без ошибок.
- `docker compose up -d` — поднимает app+infra без доп-флагов; статусы healthy.
- `docker compose --profile observability up -d` — дополнительно поднимает observability.
- `docker compose --profile observability down --remove-orphans` — корректный teardown.

## За рамками (YAGNI)

- Дробление app-сервисов по доменам (выбран один файл services).
- Изменение содержимого сервисов/портов/env (только перенос блоков).
- `project_directory`/подкаталог compose/ (выбрано размещение в корне).
