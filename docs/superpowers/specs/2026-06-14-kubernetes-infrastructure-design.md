# Kubernetes Production Infrastructure — Design

**Date:** 2026-06-14
**Status:** Approved

## Goal

Production Kubernetes infrastructure (Helm charts, GitOps, CI, secrets) for the 10-service
events platform. docker-compose stays for local development; Kubernetes is for production.

## Decisions (interview 2026-06-14)

| Topic | Decision |
|---|---|
| Stateful deps (5× Postgres, RabbitMQ) | **Managed/external** — charts consume DSN/URL from Secrets, never create the databases |
| Helm structure | **Umbrella + library chart** — one `events-common` library, thin per-service charts, `events-platform` umbrella |
| Target cluster | **Cloud-agnostic** — ingress-nginx, cert-manager, standard StorageClass; runs on any k8s |
| Observability | **Bundled** as a separate `events-observability` umbrella (kube-prometheus-stack + VictoriaLogs + Vector DaemonSet) |
| Secrets | **External Secrets Operator** backed by **Vault**. ConfigMaps hold **no values** — every runtime env var (secret AND non-secret) comes from Vault via ESO → Secret → `envFrom` |
| CI/CD | **GitOps (ArgoCD)** app-of-apps; images built/pushed by **both GitHub Actions and GitLab CI** to `ghcr.io/lelikov/<service>` |
| Migrations | **Helm pre-upgrade/pre-install Job** for DB-owning services (saver, users, notifier, shortener); app pods override `command` to skip the entrypoint migration |
| Public ingress | event-receiver (webhooks), event-admin-frontend + admin API, jitsi-chat, event-shortener (redirect). All others ClusterIP |
| Vault locally | Separate docker-compose **`vault` profile** (dev mode); normal local dev does not depend on Vault |

## Layout

All cluster infra in the ROOT repo under `deploy/` (next to `docker/`); docker-compose
unchanged for local dev.

```
deploy/
  helm/
    library/events-common/        # library chart: named templates
    charts/<service>/             # 9 thin per-service charts (depend on library)
    umbrella/events-platform/     # the 9 services + values-{prod,staging,kind}.yaml
    umbrella/events-observability/# kube-prometheus-stack + victoria-logs + vector
  argocd/                         # app-of-apps + Application manifests
  scripts/                        # Makefile + lint/template/bootstrap/smoke
```

## Helm: library + per-service + umbrella

- **`events-common`** (library): named templates for Deployment (probes `/health` liveness,
  `/ready` readiness), Service (ClusterIP), Ingress (gated), HPA (gated), ExternalSecret,
  migration Job (Helm hook), plus label/resource helpers. **No value-bearing ConfigMap.**
- **per-service charts** supply only values: image repo/tag, port (8888 internal), ingress
  on/off + host, migration on/off, replicas/resources/HPA bounds, the Vault path for its
  ExternalSecret. Deployable services: event-receiver, event-saver, event-booking,
  event-admin, event-admin-frontend, event-users, event-notifier, event-shortener,
  jitsi-chat. (event-schemas is a library, not deployed.)
- **`events-platform`** umbrella: depends on all per-service charts; `values-prod.yaml`,
  `values-staging.yaml` (stub), `values-kind.yaml` (CI smoke).

## Config & secrets (Vault-only)

- **No ConfigMap holds config values.** Every runtime env var — secret (Postgres DSNs,
  RabbitMQ URL, JWT/API keys, GetStream/UniSender/Telegram tokens, CALCOM_WEBHOOK_SECRET,
  BLACKLIST/SHORTENER keys) and non-secret (LOG_LEVEL, internal service URLs like
  `http://event-users:8888`, queue names) — lives in **Vault**.
- Per service: one `ExternalSecret` (ESO) maps a Vault path → a k8s Secret; the Deployment
  consumes it via `envFrom: secretRef`. Helm values only render k8s spec (image, replicas,
  ingress host, flags), never app config.
- **Vault** runs as its own component: in-cluster via the official Vault Helm chart (ArgoCD
  prereq); ESO `ClusterSecretStore` uses Vault with Kubernetes auth. Locally, a dev-mode
  `vault` container in the **`vault`** compose profile (off by default).

## Migrations

DB-owning services (saver, users, notifier, shortener) get a `pre-install,pre-upgrade` Helm
hook Job: same image, `command: ["alembic","upgrade","head"]`. Their Deployment overrides
`command` to run uvicorn/faststream directly (bypassing `entrypoint.sh`'s migration step), so
N replicas never migrate concurrently. Confirmed: Dockerfiles use
`ENTRYPOINT ["./entrypoint.sh"]`, overridable via k8s `command`.

## Network

ClusterIP by default. Ingress (ingress-nginx + cert-manager / Let's Encrypt ClusterIssuer,
hosts in values) only for: event-receiver, event-admin-frontend (+ admin API path),
jitsi-chat, event-shortener. TLS via cert-manager.

## Observability umbrella

`events-observability`: kube-prometheus-stack (Prometheus Operator + Grafana + Alertmanager) +
VictoriaLogs + **Vector DaemonSet** (`kubernetes_logs` source replacing docker_logs).
Migrate the existing Grafana dashboards (`events-system-overview/booking-flow/logs`), alert
rules → `PrometheusRule`, per-service scrape → `ServiceMonitor`, Alertmanager → Telegram.

## CI/CD (GitOps, dual CI)

- **GitHub Actions**: unify `publish-image.yml` across all 9 deployable service repos (3 exist,
  add 6) via a shared/reusable workflow; build+push `ghcr.io/lelikov/<service>:{sha,latest}`.
- **GitLab CI**: add `.gitlab-ci.yml` to each service repo doing the same build+push (kaniko or
  buildx), registry+creds via CI variables.
- **ArgoCD app-of-apps** (`deploy/argocd/`): Applications for prereqs (Vault, ESO,
  ingress-nginx, cert-manager), `events-platform`, `events-observability`; sync from git.
  Image tag bump = CI writes new sha into `values-prod.yaml` (PR) → ArgoCD syncs.

## Scripts

`deploy/scripts/` + Makefile: `lint` (helm lint + kubeconform), `template`, `bootstrap`
(install prereqs), `smoke` (kind).

## Verification

Per phase: `helm lint` + `helm template` + `kubeconform` (k8s schema validation) green.
Final **kind-smoke**: spin up kind; install Vault (dev) + ESO + ingress-nginx + cert-manager;
seed Vault; deploy `events-platform` with `values-kind.yaml` (tiny in-cluster Postgres/RabbitMQ
via Bitnami subcharts behind a `devDependencies` flag — managed deps are external in prod);
wait for all pods Ready; run `calcom_sim` against the event-receiver Ingress.

## Phased implementation

1. **Helm core** — library + 9 per-service charts + `events-platform` umbrella + values +
   migration Job + ExternalSecret (no ConfigMap) + Ingress + HPA + probes. Verify lint/template/kubeconform.
2. **Prereqs + secret flow** — Vault (k8s Helm app + compose `vault` profile) + ESO
   (ClusterSecretStore + ExternalSecrets) + ingress-nginx + cert-manager (ClusterIssuer).
3. **Observability umbrella** — kube-prometheus-stack + VictoriaLogs + Vector DaemonSet +
   dashboards/PrometheusRule/ServiceMonitor migration.
4. **CI + GitOps + scripts** — unify GitHub Actions, add GitLab CI to all service repos,
   ArgoCD app-of-apps, Makefile/scripts, kind-smoke.

## Out of scope

- The chosen Vault storage backend / unseal in prod (we ship Vault + auth wiring; production
  hardening, HA, auto-unseal are operator tasks).
- Real managed Postgres/RabbitMQ provisioning and production DNS.
- KEDA queue-depth autoscaling (HPA on CPU/memory for now; noted as future).
