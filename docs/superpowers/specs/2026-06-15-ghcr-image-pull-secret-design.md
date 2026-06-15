# GHCR Image Pull Secret (via Vault/ESO) — Design

**Date:** 2026-06-15
**Status:** Approved

## Goal

Let Kubernetes pods pull the private `ghcr.io/lelikov/<service>` images without local
build-and-load. The GHCR credential is delivered through Vault → External Secrets Operator
(consistent with the platform's "no values in Git" principle) as a `dockerconfigjson` secret,
and the deployments reference it via `imagePullSecrets`. Production pulls from GHCR; the kind
smoke keeps build-and-load by default with an opt-in to pull from GHCR.

## Decisions (interview 2026-06-15)

| Topic | Decision |
|---|---|
| Pod wiring | **Per-Deployment `imagePullSecrets`** rendered by the `events-common` library from a Helm value (set once via the umbrella's `global`), not a default-ServiceAccount patch |
| Credential delivery | **Vault → ESO** as a `kubernetes.io/dockerconfigjson` secret (`ghcr-pull`); the PAT never lives in Git/ConfigMaps |
| Scope | Production pulls from GHCR; **kind** keeps build+load by default with an **opt-in** to pull from GHCR when a token is provided |
| Replaces | The rejected "make packages public" path. docker-compose is unaffected (it builds locally) |

## Current state

- Each service's `ExternalSecret` (`_externalsecret.tpl`) maps a Vault path → a `<fullname>-env`
  secret consumed via `envFrom`. The store is the `vault-backend` `ClusterSecretStore`
  (Vault Kubernetes auth), ESO API `external-secrets.io/v1beta1`.
- `_deployment.tpl` and `_migration-job.tpl` render the workload pods; **neither sets
  `imagePullSecrets`**. There is no ServiceAccount template.
- Per-service values set `image.repository: ghcr.io/lelikov/<service>`. In a real cluster these
  are private → `ImagePullBackOff` without a pull secret (the earlier kind smoke only worked via
  local build + `kind load`).
- `seed-vault.sh` seeds `secret/events/<service>` per service; `smoke.sh` builds+loads images.

## Components

### 1. Vault path `secret/events/ghcr`
Two keys: `username` (GitHub username) and `token` (a PAT with `read:packages`). Seeded by
`seed-vault.sh` from env `GHCR_USERNAME` / `GHCR_TOKEN`. In the kind smoke this path is seeded
**only** when those env vars are set (opt-in); otherwise skipped so build+load remains the path.

### 2. `ghcr-pull` ExternalSecret (one per namespace)
A new umbrella template `deploy/helm/umbrella/events-platform/templates/ghcr-pull-externalsecret.yaml`,
gated by `.Values.ghcrPullSecret.enabled` (default false; true in `values-prod.yaml`). It maps the
Vault `secret/events/ghcr` keys into a `kubernetes.io/dockerconfigjson` secret named `ghcr-pull`
using ESO's `target.template`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ghcr-pull
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: vault-backend
  target:
    name: ghcr-pull
    creationPolicy: Owner
    template:
      type: kubernetes.io/dockerconfigjson
      data:
        .dockerconfigjson: '{"auths":{"ghcr.io":{"username":"{{ .username }}","password":"{{ .token }}","auth":"{{ printf "%s:%s" .username .token | b64enc }}"}}}'
  data:
    - secretKey: username
      remoteRef: { key: secret/events/ghcr, property: username }
    - secretKey: token
      remoteRef: { key: secret/events/ghcr, property: token }
```

Store kind/name are overridable to match the `_externalsecret.tpl` defaults
(`ClusterSecretStore` / `vault-backend`).

### 3. Library chart — render `imagePullSecrets`
`events-common/templates/_deployment.tpl` **and** `_migration-job.tpl` (the migration Job pulls the
same image) render, in the pod spec:

```yaml
{{- with (.Values.global).imagePullSecrets | default .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
{{- end }}
```

Reading `.Values.global.imagePullSecrets` first lets the umbrella set it once for all 9 subcharts;
a per-chart `.Values.imagePullSecrets` override still works.

### 4. Umbrella values
- `values-prod.yaml`: `global: { imagePullSecrets: [{ name: ghcr-pull }] }` and
  `ghcrPullSecret: { enabled: true }`.
- `values-kind.yaml`: `ghcrPullSecret: { enabled: false }` and no `global.imagePullSecrets`
  (build+load). Opt-in via `--set ghcrPullSecret.enabled=true --set global.imagePullSecrets[0].name=ghcr-pull`.
- `values.yaml` (default): `ghcrPullSecret: { enabled: false }` so a bare install renders nothing new.

### 5. kind smoke opt-in (`smoke.sh`)
When `GHCR_USERNAME` and `GHCR_TOKEN` are both set: seed `secret/events/ghcr`, install the platform
with `ghcrPullSecret.enabled=true` + `global.imagePullSecrets`, and **skip** the build+load loop
(pull from GHCR). Otherwise: unchanged (build+load, pull secret disabled). Default behaviour and
the no-token contributor experience do not change.

### 6. Documentation
`deploy/helm/README.md` (or `prereqs/README.md`): how to create the `read:packages` PAT, the Vault
path/keys, and that prod pulls from GHCR via `ghcr-pull`. `deploy/helm/LOCAL_DEBUGGING.md`: the
kind opt-in (`GHCR_USERNAME=… GHCR_TOKEN=… make -C deploy/scripts smoke`). Note that
making the packages public is the rejected alternative.

## Verification

`make -C deploy/scripts lint` (helm lint + kubeconform) passes with 0 failures. `helm template` of
`events-platform` with `values-prod.yaml` shows: every Deployment and every migration Job carries
`imagePullSecrets: [{ name: ghcr-pull }]`, and exactly one `dockerconfigjson` `ghcr-pull`
ExternalSecret is rendered. With `values-kind.yaml` (no token) none of these render. A live kind
opt-in run (`GHCR_USERNAME`/`GHCR_TOKEN` set) pulling the private images instead of build+load is
the optional end-to-end check (requires a real PAT).

## Out of scope

- Making the GHCR packages public (explicitly rejected).
- docker-compose (builds images locally; no pull secret needed).
- PAT rotation/expiry automation and GHCR-package↔repo linking.
- Per-service distinct pull credentials (one shared `ghcr-pull` covers all images).
