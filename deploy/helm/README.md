# Events Platform — Helm Charts

Production Kubernetes packaging for the events platform. `docker-compose`
remains the local-dev story; these charts are for cluster deployment.

> **Phase 1 is the Helm core:** library chart, 9 thin per-service charts, the
> `events-platform` umbrella. **Phase 2 (`prereqs/`) adds the runtime
> prerequisites** that make a deploy actually *work* — Vault, External Secrets
> Operator, ingress-nginx, cert-manager — plus the `vault-backend`
> ClusterSecretStore, Let's Encrypt ClusterIssuers, and the Vault seed script.

## Deploy order (kind / any cluster)

The platform pods only become Ready once the ESO-managed `<release>-env` Secrets
exist, which requires the prereqs + a seeded Vault. End-to-end:

1. **Install prereqs** — cert-manager → ingress-nginx → Vault → ESO, then apply
   the ClusterIssuers + ClusterSecretStore. See **[`prereqs/README.md`](prereqs/README.md)**.
2. **Seed Vault** — `VAULT_ADDR=... VAULT_TOKEN=... deploy/scripts/seed-vault.sh`
   writes `secret/events/<service>` from the repo's `.env.example` dev defaults
   (resolved as `secret/data/events/<service>` by each ExternalSecret).
3. **Deploy the platform** — `helm dependency build` the umbrella, then
   `helm upgrade --install events-platform umbrella/events-platform -f <overlay>`.

For a quick LOCAL test of just the secret flow (no cluster), use the
docker-compose `vault` profile (dev mode, OFF by default):

```bash
docker compose --profile vault up -d vault
USE_DOCKER_VAULT=1 VAULT_TOKEN=dev-root-token deploy/scripts/seed-vault.sh
docker compose --profile vault down -v
```

## Layout

```
deploy/helm/
  library/events-common/      # Helm LIBRARY chart (type: library) — named templates only
  charts/<service>/           # 9 thin per-service charts; depend on the library via file://
  umbrella/events-platform/   # umbrella over all 9 charts + env overlays
  .gen_charts.sh              # regenerates the per-service charts from the values matrix
```

### Library chart — `library/events-common`

No deployable resources of its own; it exposes named templates that the
per-service charts `include`:

| Template | Renders | Gated by |
|---|---|---|
| `events-common.deployment` | `apps/v1` Deployment | always |
| `events-common.service` | ClusterIP Service | always |
| `events-common.ingress` | `networking.k8s.io/v1` Ingress (cert-manager annotation, TLS) | `.Values.ingress.enabled` |
| `events-common.hpa` | `autoscaling/v2` HPA on CPU + memory | `.Values.hpa.enabled` |
| `events-common.externalsecret` | `external-secrets.io/v1beta1` ExternalSecret | `.Values.externalSecret.enabled` (default true) |
| `events-common.migrationJob` | pre-install/pre-upgrade hook Job (`alembic upgrade head`) | `.Values.migration.enabled` |
| `events-common.fullname` / `.labels` / `.selectorLabels` / `.envSecretName` | helpers | — |

The Deployment sets `securityContext` (`runAsNonRoot`, drop ALL caps,
`allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`), liveness +
readiness `httpGet` probes, and resource requests/limits. **All env comes from
one Secret via `envFrom: secretRef` — there is no value-bearing ConfigMap
anywhere in these charts.**

### Per-service charts — `charts/<service>`

Each chart is `dependencies: [events-common @ file://../../library/events-common]`
plus a `values.yaml` and a single `templates/all.yaml` that includes each
library template. They carry **only k8s spec** (image, port, probes, ingress
host/flag, replicas, resources, HPA bounds, the Vault path) — never app config.

| service | port | ingress (default host) | readiness | migration Job | deploy command override |
|---|---|---|---|---|---|
| event-receiver | 8888 | enabled — `receiver.example.com` | `/ready` | no | — |
| event-saver | 8888 | disabled | `/ready` | **yes** | uvicorn (skip entrypoint migrate) |
| event-booking | 8888 | disabled | `/ready` | no | — |
| event-admin | 8888 | enabled — `admin-api.example.com` | `/ready` | no | — |
| event-admin-frontend | 80 | enabled — `admin.example.com` | `/health` | no | — |
| event-users | 8888 | disabled | `/ready` | **yes** | uvicorn (skip entrypoint migrate) |
| event-notifier | 8888 | disabled | `/ready` | **yes** | uvicorn (skip entrypoint migrate) |
| event-shortener | 8888 | enabled — `s.example.com` | `/ready` | **yes** | uvicorn (skip entrypoint migrate) |
| jitsi-chat | 80 | enabled — `meet.example.com` | `/health` | no | — |

The hosts above are **placeholders** overridden by the umbrella env overlays.
`image.repository` defaults to `ghcr.io/lelikov/<service>`, `image.tag` to
`latest` (CI overrides per env).

DB-owning services (saver, users, notifier, shortener) run their migration as
a Helm **pre-install/pre-upgrade hook Job** and override the Deployment
`command` to launch uvicorn directly, bypassing `entrypoint.sh`'s `alembic
upgrade head` — so N replicas never migrate concurrently.

### Umbrella — `umbrella/events-platform`

Depends on all 9 per-service charts. Per-subchart values are keyed by chart
name; the overlays set `image.tag`, `ingress.host`, `replicas`, and `hpa`:

- `values.yaml` — base defaults (replicas 1, tag `latest`).
- `values-prod.yaml` — replicas 2, HPA enabled, hostnames + `image.tag` are
  `TODO-*` placeholders (CI writes the image sha; DNS is set on rollout).
- `values-staging.yaml` — single-replica stub, placeholder hosts.
- `values-kind.yaml` — replicas 1, ingress hosts on `*.127.0.0.1.nip.io`,
  TLS/cert-manager disabled. `externalSecret` stays enabled (matches prod) but
  **requires the phase-2 ESO + Vault prereqs** before pods become Ready.

## Key values knobs

```yaml
replicas: 1                       # ignored when hpa.enabled (HPA owns scaling)
image: { repository, tag, pullPolicy }
containerPort: 8888
command: []                       # Deployment command override (migration bypass)
probes: { liveness: /health, readiness: /ready, initialDelaySeconds, periodSeconds }
resources: { requests: {cpu,memory}, limits: {cpu,memory} }
externalSecret:
  enabled: true
  refreshInterval: 1h
  storeKind: ClusterSecretStore
  storeName: vault-backend        # phase-2 ClusterSecretStore name
  vaultPath: secret/data/events/<service>
ingress: { enabled, className, clusterIssuer, host, path, pathType, tls: {enabled, secretName} }
hpa: { enabled, minReplicas, maxReplicas, targetCPUUtilizationPercentage, targetMemoryUtilizationPercentage }
migration: { enabled, backoffLimit, command }
```

## Secrets & config — Vault via ESO (phase 2)

Every runtime env var — **secret** (Postgres DSNs, RabbitMQ URL, JWT/API keys,
GetStream/UniSender/Telegram tokens, etc.) **and non-secret** (`LOG_LEVEL`,
internal URLs like `http://event-users:8888`, queue names) — lives in **Vault**.
Nothing app-related is in Helm values or a ConfigMap.

For each service the `events-common.externalsecret` template renders an
ExternalSecret that:

- references a **`ClusterSecretStore` named `vault-backend`** (phase 2 creates
  it, backed by Vault with Kubernetes auth),
- extracts the whole Vault path **`secret/data/events/<service>`** (override via
  `externalSecret.vaultPath`),
- writes a k8s Secret named **`<release-fullname>-env`** (the
  `events-common.envSecretName` convention).

The Deployment and the migration Job both consume that exact Secret via
`envFrom: secretRef`. **Phase 2 must populate the `vault-backend`
ClusterSecretStore and seed the `secret/data/events/<service>` paths**, or pods
will stay NotReady because the env Secret won't exist.

## Validate

```bash
brew install helm kubeconform     # or run via official docker images

# library is file-based; build deps for each per-service chart + umbrella
for c in charts/* umbrella/events-platform; do helm dependency build "$c"; done

# lint everything
helm lint library/events-common charts/* umbrella/events-platform

# render + schema-validate (ExternalSecret CRD is skipped via -ignore-missing-schemas)
helm template events-platform umbrella/events-platform -f umbrella/events-platform/values-prod.yaml \
  | kubeconform -ignore-missing-schemas -summary -strict
helm template events-platform umbrella/events-platform -f umbrella/events-platform/values-kind.yaml \
  | kubeconform -ignore-missing-schemas -summary -strict

# confirm NO config ConfigMap is generated (only Secret/ExternalSecret carry env)
helm template events-platform umbrella/events-platform -f umbrella/events-platform/values-prod.yaml \
  | grep -c 'kind: ConfigMap'   # -> 0
```

## Regenerating per-service charts

`charts/<service>` Chart/values are generated from the matrix in
`.gen_charts.sh`. Edit the matrix and re-run `bash .gen_charts.sh` to
regenerate; the library templates are hand-maintained.
