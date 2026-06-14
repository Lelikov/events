# Локальная отладка Helm-чартов на kind

Гайд по ручному итеративному циклу отладки k8s-инфраструктуры на локальном
**kind**-кластере. Для разработки самих сервисов используйте `docker compose`
(см. корневой `README.md`) — kind нужен только когда отлаживаете **чарты,
манифесты, probes, Ingress, поток Vault→ESO, миграции**.

> Полностью автоматический прогон — `make -C deploy/scripts smoke` (создаёт
> кластер, ставит prereqs, сидит Vault, разворачивает платформу, бьёт вебхуком,
> сносит кластер). Этот документ — про **ручной** цикл, когда нужно остаться в
> кластере и итерироваться.

## Что нужно

```bash
brew install kind helm kubectl kubeconform   # один раз
docker info >/dev/null                         # Docker должен быть запущен
```

Статическая проверка без кластера (быстрый цикл правок):

```bash
make -C deploy/scripts lint       # helm lint всех чартов + kubeconform
make -C deploy/scripts template   # отрендерить umbrella'ы
```

## Самый быстрый путь: оставить smoke-кластер живым

`smoke.sh` умеет переиспользовать уже существующий кластер и не удалять его:

```bash
KEEP=1 bash deploy/scripts/smoke.sh     # поднять всё и НЕ сносить
# ... отлаживаемся в кластере events-smoke ...
kind delete cluster --name events-smoke # снести вручную, когда закончили
```

Дальше — то же самое по шагам, если нужен контроль над каждым этапом.

## Ручной цикл по шагам

### 1. Кластер

```bash
kind create cluster --name events-smoke --wait 120s
kubectl config use-context kind-events-smoke
```

### 2. Образы сервисов в кластер

В kind нет доступа к приватному GHCR — грузим локально собранные образы:

```bash
docker compose build                      # собрать все образы (контексты уже настроены)
for s in event-receiver event-saver event-booking event-admin event-admin-frontend \
         event-users event-notifier event-shortener jitsi-chat; do
  docker tag "events-$s" "ghcr.io/lelikov/$s:latest"
  kind load docker-image "ghcr.io/lelikov/$s:latest" --name events-smoke
done
```

`values-kind.yaml` ставит `imagePullPolicy: IfNotPresent`, поэтому загруженные
образы используются без обращения к реестру. Если тег не `latest` — поправьте
`image.tag` в overlay или передайте `--set`.

### 3. Prereqs (cert-manager, ingress-nginx, Vault-dev, ESO)

```bash
make -C deploy/scripts bootstrap          # ставит prereqs в правильном порядке
```

> cert-manager тянется через OCI (`oci://quay.io/jetstack/charts/cert-manager`) —
> http-индекс jetstack троттлит. Vault в kind поднимается в **dev-режиме**
> (root-токен `root`, без unseal). В проде — file-storage + Kubernetes auth
> (см. `prereqs/vault-bootstrap.md`).

### 4. Засеять Vault

ESO материализует Secret'ы из Vault — без сидов поды не станут Ready
(`ExternalSecret` не разрезолвится):

```bash
kubectl -n vault port-forward svc/vault 8200:8200 &
VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=root \
  bash deploy/scripts/seed-vault.sh        # пишет secret/events/<service>
```

DSN'ы Postgres/RabbitMQ в kind указывают на in-cluster devDependencies (см. шаг 5).
Проверить: `vault kv get secret/events/event-saver`.

### 5. Развернуть платформу

```bash
helm dependency build deploy/helm/umbrella/events-platform
helm upgrade --install events-platform deploy/helm/umbrella/events-platform \
  -n events-platform --create-namespace \
  -f deploy/helm/umbrella/events-platform/values-kind.yaml --wait --timeout 6m
```

`values-kind.yaml` включает `devDependencies` (in-cluster Postgres + RabbitMQ:
хосты `events-platform-devPostgresql.events-platform.svc:5432` и
`events-platform-devRabbitmq...:5672`) и **отключает migration-Job** (Bitnami-БД
поднимается позже Helm pre-upgrade хука). Поэтому схемы БД в kind нет — для
проверки самих чартов это норма; для проверки записи в БД примените миграции
вручную:

```bash
kubectl -n events-platform exec deploy/events-platform-event-saver -- alembic upgrade head
```

### 6. Проверить и поитерироваться

```bash
kubectl get pods -n events-platform
kubectl -n events-platform logs deploy/events-platform-event-receiver
```

Дёрнуть ingress event-receiver (через port-forward или nip.io-хост
`receiver.127.0.0.1.nip.io`):

```bash
kubectl -n events-platform port-forward svc/events-platform-event-receiver 8888:8888 &
# подписать BOOKING_CREATED из event-booking/requests.jsonl и POST на /event/calcom
```

Цикл правки чарта → быстрый повтор без пересоздания кластера:

```bash
helm upgrade events-platform deploy/helm/umbrella/events-platform \
  -n events-platform -f .../values-kind.yaml
# если меняли код сервиса — пересобрать и перезагрузить образ:
docker compose build event-saver
docker tag events-event-saver ghcr.io/lelikov/event-saver:latest
kind load docker-image ghcr.io/lelikov/event-saver:latest --name events-smoke
kubectl -n events-platform rollout restart deploy/events-platform-event-saver
```

### 7. Снести

```bash
kind delete cluster --name events-smoke
```

## Частые грабли

| Симптом | Причина / решение |
|---|---|
| `ImagePullBackOff` | Образ не загружен в kind или `pullPolicy: Always`. `kind load` + `IfNotPresent`. |
| Под висит `0/1`, ExternalSecret не Ready | Vault не засеян или ESO/ClusterSecretStore не готов. Шаг 4; `kubectl describe externalsecret -n events-platform`. |
| Pre-upgrade migration-Job падает | В kind миграции выключены намеренно; включать только когда БД уже поднята. |
| Ingress не отвечает | В kind нет LoadBalancer — используйте `port-forward` или nip.io-хосты; проверьте, что ingress-nginx Ready. |
| cert-manager долго ставится | Тяните через OCI `oci://quay.io/jetstack/charts/cert-manager`, не через helm-репо. |

## Связанные документы

- `deploy/helm/README.md` — структура чартов и values
- `deploy/helm/prereqs/README.md` — установка prereqs, `vault-bootstrap.md`
- `deploy/argocd/README.md` — GitOps-разворачивание в проде
- `deploy/scripts/Makefile` — `lint / template / bootstrap / seed / smoke / clean`
