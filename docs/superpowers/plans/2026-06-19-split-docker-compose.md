# Разбиение docker-compose.yml через include — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разбить `docker-compose.yml` на тонкий root с `include:` + три файла по слоям (infra / services / observability), не меняя поведения.

**Architecture:** Блоки сервисов переносятся дословно в `docker-compose.{infra,services,observability}.yml` в корне; `docker-compose.yml` становится `include:` этих трёх файлов. Эквивалентность доказывается идентичным `docker compose config` до и после.

**Tech Stack:** Docker Compose v5.1.2 (`include:`), YAML.

**Spec:** `docs/superpowers/specs/2026-06-19-split-docker-compose-design.md`

---

## Conventions

- Один репозиторий — root `events`. Перед стартом создать ветку (репо на `main`):
  ```bash
  cd /Users/alexandrlelikov/PycharmProjects/events && git checkout -b refactor/split-compose
  ```
- Коммиты заканчиваются:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **НЕ пушить** (merge/push — отдельный user-gated шаг).
- «Тест» — `docker compose config` (Docker есть). Главный критерий — нормализованный config до == после.
- Это перенос блоков **дословно**: НЕ редактировать содержимое сервисов (порты/env/build/depends_on), только переместить.

## Распределение сервисов по файлам

| Файл | services | volumes |
|---|---|---|
| `docker-compose.infra.yml` | rabbitmq, postgres, mocks, vault | pg-data |
| `docker-compose.services.yml` | event-receiver, event-saver, event-booking, event-users, event-admin, event-notifier, event-db-sync, event-shortener, event-admin-frontend, jitsi-chat | — |
| `docker-compose.observability.yml` | pg-exporter, prometheus, grafana, alertmanager, victorialogs, vector, tempo, otel-collector | prometheus-data, alertmanager-data, victorialogs-data, tempo-data |

---

### Task 1: Зафиксировать baseline + создать три файла-слоя

**Files:**
- Create: `docker-compose.infra.yml`, `docker-compose.services.yml`, `docker-compose.observability.yml`
- (read) `docker-compose.yml`

- [ ] **Step 1: Baseline нормализованного конфига** (для сравнения в конце)
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose config > /tmp/compose-before.yml 2>/dev/null && echo "baseline saved ($(wc -l </tmp/compose-before.yml) lines)"
```
Expected: файл сохранён, без ошибок.

- [ ] **Step 2: ПРОЧИТАТЬ `docker-compose.yml` целиком.** Определить точные границы каждого блока сервиса
  (по отступу `^  <name>:`) и блока `volumes:`. Блоки идут в порядке: rabbitmq, postgres, mocks,
  pg-exporter, prometheus, grafana, alertmanager, victorialogs, vector, tempo, otel-collector, vault,
  event-receiver, event-saver, event-booking, event-users, event-admin, event-notifier, event-db-sync,
  event-shortener, event-admin-frontend, jitsi-chat; затем top-level `volumes:`.

- [ ] **Step 3: `docker-compose.infra.yml`** — собрать файл вида:
```yaml
# Infra/data layer — shared backing services. Included by docker-compose.yml.
services:
  <блок rabbitmq дословно>
  <блок postgres дословно>
  <блок mocks дословно>
  <блок vault дословно>

volumes:
  pg-data:
```
Перенести блоки `rabbitmq`, `postgres`, `mocks`, `vault` ДОСЛОВНО (с их комментариями-заголовками и
отступами в 2 пробела). `pg-data:` — взять из исходного top-level `volumes:` (сохранив его подсодержимое,
если есть; если просто `pg-data:` без тела — так и оставить).

- [ ] **Step 4: `docker-compose.services.yml`** — собрать:
```yaml
# Application services. Included by docker-compose.yml.
services:
  <блоки 10 app-сервисов дословно, в исходном порядке>
```
Перенести `event-receiver`, `event-saver`, `event-booking`, `event-users`, `event-admin`,
`event-notifier`, `event-db-sync`, `event-shortener`, `event-admin-frontend`, `jitsi-chat` дословно.
Блока `volumes:` в этом файле НЕТ (app-сервисы именованных томов не используют).

- [ ] **Step 5: `docker-compose.observability.yml`** — собрать:
```yaml
# Observability stack (profile: observability). Included by docker-compose.yml.
services:
  <блоки pg-exporter, prometheus, grafana, alertmanager, victorialogs, vector, tempo, otel-collector дословно>

volumes:
  prometheus-data:
  alertmanager-data:
  victorialogs-data:
  tempo-data:
```
Перенести 8 obs-сервисов дословно (их `profiles: ["observability"]` сохранить как есть) и 4 их тома из
исходного top-level `volumes:`.

- [ ] **Step 6: Проверка, что все блоки распределены** (ничего не потеряно/не задвоено)
```bash
# каждый сервис должен встречаться ровно один раз среди трёх новых файлов
for s in rabbitmq postgres mocks vault pg-exporter prometheus grafana alertmanager victorialogs vector tempo otel-collector event-receiver event-saver event-booking event-users event-admin event-notifier event-db-sync event-shortener event-admin-frontend jitsi-chat; do
  n=$(grep -hE "^  ${s}:" docker-compose.infra.yml docker-compose.services.yml docker-compose.observability.yml 2>/dev/null | wc -l | tr -d ' ')
  [ "$n" = "1" ] || echo "PROBLEM: $s found $n times"
done
echo "service distribution checked"
# тома
for v in pg-data prometheus-data alertmanager-data victorialogs-data tempo-data; do
  grep -hE "^  ${v}:" docker-compose.infra.yml docker-compose.observability.yml >/dev/null || echo "MISSING volume $v"
done
echo "volumes checked"
```
Expected: «service distribution checked» / «volumes checked» без строк PROBLEM/MISSING.

- [ ] **Step 7: Commit (файлы-слои; docker-compose.yml пока НЕ трогаем)**
```bash
git add docker-compose.infra.yml docker-compose.services.yml docker-compose.observability.yml
git commit -m "refactor(compose): extract infra/services/observability layer files

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Заменить docker-compose.yml на include + доказать эквивалентность

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Сохранить верхний комментарий-шапку** исходного `docker-compose.yml` (строки 1-13, если
  это пояснения про стек) — перенести в начало нового тонкого файла. Заменить тело `docker-compose.yml` на:
```yaml
# <сохранённый верхний комментарий, если был>
#
# This file is intentionally thin: the stack is split by layer into the
# docker-compose.*.yml files below and merged via `include`. `docker compose up`
# works unchanged; `--profile observability` still brings up the observability stack.
include:
  - docker-compose.infra.yml
  - docker-compose.services.yml
  - docker-compose.observability.yml
```
(Никаких `services:`/`volumes:` в корневом файле больше нет.)

- [ ] **Step 2: Конфиг валиден + ЭКВИВАЛЕНТЕН baseline**
```bash
docker compose config -q 2>&1 && echo "CONFIG OK"
docker compose config > /tmp/compose-after.yml 2>/dev/null
diff <(sort /tmp/compose-before.yml) <(sort /tmp/compose-after.yml) && echo "IDENTICAL (sorted)" || echo "DIFF FOUND (см. ниже)"
diff /tmp/compose-before.yml /tmp/compose-after.yml | head -40
```
Expected: `CONFIG OK`; `IDENTICAL (sorted)`. Прямой `diff` тоже должен быть пустым (порядок сервисов в
merged-выводе обычно стабилен — алфавитный). Если расхождения ТОЛЬКО в порядке ключей/сервисов, но
sorted-diff пуст — это эквивалентность (порядок в config не семантичен). Если есть РЕАЛЬНЫЕ отличия
(добавились/пропали поля, изменились значения) — STOP, BLOCKED: какой-то блок перенесён неточно.

- [ ] **Step 3: Профили на месте** (observability skip без флага, поднимается с флагом)
```bash
# без профиля obs-сервисов в config быть НЕ должно как запускаемых; но `config` их показывает.
# Проверим через --services (учитывает профили):
docker compose config --services | sort | tr '\n' ' '; echo
docker compose --profile observability config --services | sort | tr '\n' ' '; echo
```
Expected: первый список — БЕЗ obs-сервисов (prometheus/grafana/… отсутствуют); второй — С obs-сервисами.
(Так подтверждается, что `profiles` сохранились при сплите.)

- [ ] **Step 4: Commit**
```bash
git add docker-compose.yml
git commit -m "refactor(compose): docker-compose.yml is now a thin include of the layer files

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Live smoke (CONTROLLER, Docker)

> Стек, возможно, уже запущен. Проверяем, что сплит-конфиг реально поднимает систему.

- [ ] **Step 1: Поднять основной стек без флагов**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d 2>&1 | tail -5
docker compose ps --format '{{.Service}} {{.Status}}' | grep -ivE 'healthy|Up ' || echo "ALL HEALTHY/UP"
```
Expected: все app+infra сервисы healthy; нет restart/exited.

- [ ] **Step 2: Observability-профиль поднимается**
```bash
docker compose --profile observability up -d 2>&1 | tail -4
docker compose --profile observability ps --services | sort | tr '\n' ' '; echo
```
Expected: prometheus/grafana/tempo/… поднялись.

- [ ] **Step 3: (опц.) Свернуть observability обратно, оставив app**
```bash
docker compose --profile observability down --remove-orphans 2>&1 | tail -2 || true
```
(По желанию — или оставить всё запущенным для дальнейшей ручной проверки.)

- [ ] **Step 4:** Результат — контроллеру (smoke passed). Коммитить нечего.

---

## Self-review

- **Spec coverage:** три файла-слоя (T1), тонкий include-root (T2), эквивалентность config до/после (T2),
  профили сохранены (T2 Step 3), live smoke + observability (T3). ✅
- **Placeholder scan:** конкретные команды/структура; перенос блоков «дословно» — не плейсхолдер, а
  требование точности (проверяется config-диффом). ✅
- **Распределение:** каждый из 22 сервисов ровно в одном файле (проверка T1 Step 6); 5 томов распределены
  (pg-data→infra, 4 obs-тома→observability; services без томов). ✅
- **Риск порядка в diff:** учтён — sorted-diff как критерий эквивалентности; реальные отличия → BLOCKED. ✅
- **Пути/depends_on/профили:** файлы в корне (пути не меняются), include мёржит до валидации (кросс-файловые
  depends_on ок), profiles переносятся дословно. ✅
