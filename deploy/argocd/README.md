# ArgoCD app-of-apps (phase 4)

GitOps entrypoint for the events platform. One root `Application`
(`app-of-apps.yaml`) points ArgoCD at `apps/`; every child Application there is
created and synced automatically. Sync-waves order the rollout so dependencies
come up first.

## Files

```
argocd/
  app-of-apps.yaml              # root Application -> apps/ (apply this ONCE)
  apps/
    00-cert-manager.yaml        # wave 0  (OCI oci://quay.io/jetstack/charts/cert-manager)
    01-ingress-nginx.yaml       # wave 1
    01-vault.yaml               # wave 1
    02-external-secrets.yaml    # wave 2  (ESO, pinned 0.10.x for v1beta1)
    03-events-platform.yaml     # wave 3  (in-repo umbrella, values-prod.yaml)
    03-events-observability.yaml# wave 3  (in-repo umbrella, values-prod.yaml)
```

## Sync-wave order

```
wave 0: cert-manager
wave 1: ingress-nginx, vault          (parallel)
wave 2: external-secrets (ESO)
wave 3: events-platform, events-observability   (parallel)
```

cert-manager first (TLS issuers depend on it). ingress-nginx + Vault next
(independent). ESO after Vault so its CRDs exist before the platform renders
ExternalSecrets. Platform + observability last.

## Placeholders to replace

| Placeholder | Where | Set to |
|---|---|---|
| `repoURL: https://github.com/lelikov/events` | `app-of-apps.yaml`, `03-events-*.yaml` | your repo URL |
| `targetRevision: main` | same files | branch/tag ArgoCD tracks |
| destination `namespace` | per Application | as desired (defaults: cert-manager, ingress-nginx, vault, external-secrets, events-platform, observability) |

`prereqs` charts (cert-manager/ingress-nginx/vault/ESO) install from their
upstream Helm repos with values inlined in each Application. Keep those inline
values in sync with `deploy/helm/prereqs/*-values.yaml`.

## ServerSideApply / ignoreDifferences

- **cert-manager, ESO, events-platform, events-observability** use
  `ServerSideApply=true` — their CRDs (and the platform's rendered manifests)
  exceed the client-side-apply annotation size limit.
- **events-observability** also sets `ignoreDifferences` for CRD `caBundle` /
  `status` and webhook `caBundle` fields that cert-manager / the Prometheus
  operator mutate at runtime, so ArgoCD does not flap to OutOfSync.

## The Vault gotcha (init -> seed -> sync)

ArgoCD installs Vault but **cannot init / unseal / seed** it. After the `vault`
Application is Healthy and **before** the platform pods can become Ready, an
operator must run the manual one-time bootstrap:

1. **init + unseal** Vault and enable KV-v2, write the `events-read` policy, and
   enable + bind the Kubernetes auth role `events`
   (`deploy/helm/prereqs/manifests/vault-bootstrap.md`). *(A dev-mode Vault —
   the kind smoke — auto-initializes + unseals, so this step is skipped there.)*
2. **apply the ClusterSecretStore + ClusterIssuers** (these are intentionally
   NOT ArgoCD-managed, so a not-yet-unsealed Vault never blocks the ESO sync):
   ```bash
   kubectl apply -f deploy/helm/prereqs/manifests/cluster-issuer.yaml
   kubectl apply -f deploy/helm/prereqs/manifests/cluster-secret-store.yaml
   ```
3. **seed Vault** with each service's env:
   ```bash
   VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<root> deploy/scripts/seed-vault.sh
   # port-forward first: kubectl -n vault port-forward svc/vault 8200:8200
   ```

Only after step 3 do the ExternalSecrets resolve into `<release>-env` Secrets
and the platform Deployments become Ready. Until then the wave-3 platform
Application will show pods `CrashLoopBackOff`/`CreateContainerConfigError` — this
is expected and self-resolves once Vault is seeded (ESO refresh interval 1h, or
trigger a manual refresh).

## Bootstrap

```bash
# 0. Install ArgoCD itself (out of scope here):
#    helm install argocd argo/argo-cd -n argocd --create-namespace

# 1. Edit repoURL/targetRevision placeholders in app-of-apps.yaml + apps/03-*.

# 2. Apply the root app once. ArgoCD does the rest, wave by wave.
kubectl apply -n argocd -f deploy/argocd/app-of-apps.yaml

# 3. When the `vault` Application is Healthy, run the Vault gotcha steps above.

# 4. Watch sync:
#    argocd app list
#    argocd app sync events-platform   # (or rely on automated sync)
```
