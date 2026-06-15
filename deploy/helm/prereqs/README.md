# Cluster prerequisites (phase 2)

The four cluster-level components the `events-platform` umbrella depends on, plus
the manifests that wire them together. Install these **before** deploying the
platform. (Phase 4 turns these into ArgoCD Applications; here they are plain
`helm upgrade --install` + `kubectl apply`.)

| Component | Chart | Pinned version | App version | Namespace |
|---|---|---|---|---|
| cert-manager | `jetstack/cert-manager` | `v1.19.5` | 1.19.5 | `cert-manager` |
| ingress-nginx | `ingress-nginx/ingress-nginx` | `4.15.1` | 1.15.1 | `ingress-nginx` |
| Vault | `hashicorp/vault` | `0.30.1` | 1.20.1 | `vault` |
| External Secrets Operator | `external-secrets/external-secrets` | `0.10.7` | v0.10.7 | `external-secrets` |

> **ESO is pinned to the 0.10.x line on purpose** — it serves the
> `external-secrets.io/v1beta1` API the platform charts render. ESO 2.x promotes
> those CRDs to `v1` and removes `v1beta1`; bump both together if you upgrade.

## Install order

cert-manager → ingress-nginx → Vault → ESO. (ingress-nginx must exist before the
ACME http01 ClusterIssuers resolve; Vault must be up + seeded before ESO's
ClusterSecretStore validates.)

```bash
# 1. Add + update repos
helm repo add jetstack https://charts.jetstack.io
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

# 2. cert-manager (CRDs installed via values installCRDs: true)
helm upgrade --install cert-manager jetstack/cert-manager \
  --version v1.19.5 --namespace cert-manager --create-namespace \
  -f deploy/helm/prereqs/cert-manager-values.yaml

# 3. ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --version 4.15.1 --namespace ingress-nginx --create-namespace \
  -f deploy/helm/prereqs/ingress-nginx-values.yaml

# 4. Vault (single-instance, file storage — NOT dev/in-memory)
helm upgrade --install vault hashicorp/vault \
  --version 0.30.1 --namespace vault --create-namespace \
  -f deploy/helm/prereqs/vault-values.yaml

# 5. External Secrets Operator (CRDs installed via values installCRDs: true)
helm upgrade --install external-secrets external-secrets/external-secrets \
  --version 0.10.7 --namespace external-secrets --create-namespace \
  -f deploy/helm/prereqs/eso-values.yaml
```

## After the charts are up

```bash
# 6. Bootstrap Vault: init/unseal, enable KV-v2, write policy, k8s auth role.
#    Full steps + commands: manifests/vault-bootstrap.md
#    (dev-mode local Vault skips init/unseal — see that doc).

# 7. Apply the Let's Encrypt issuers (edit the TODO email first!)
kubectl apply -f deploy/helm/prereqs/manifests/cluster-issuer.yaml

# 8. Apply the ClusterSecretStore (Vault must be reachable + the k8s auth role set)
kubectl apply -f deploy/helm/prereqs/manifests/cluster-secret-store.yaml

# 9. Seed Vault with each service's env (dev defaults from .env.example)
VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<root> deploy/scripts/seed-vault.sh
#    (port-forward Vault first: kubectl -n vault port-forward svc/vault 8200:8200)

# Now deploy the platform (see ../README.md).
```

### GHCR image pull secret

Private `ghcr.io/lelikov/*` images are pulled in prod via a Vault-sourced
`dockerconfigjson` secret (`ghcr-pull`) materialized by ESO. To enable it:

1. **Create a GitHub PAT** with the `read:packages` scope.

2. **Store the credential in Vault** — either directly:
   ```bash
   vault kv put secret/events/ghcr username=Lelikov token=<pat>
   ```
   or via environment variables when running `seed-vault.sh`:
   ```bash
   GHCR_USERNAME=Lelikov GHCR_TOKEN=<pat> \
     VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<root> \
     deploy/scripts/seed-vault.sh
   ```
   (The script seeds `secret/events/ghcr` only when `GHCR_TOKEN` is set; it
   prints a skip message otherwise so the default build+load path is unaffected.)

3. **Prod** (`values-prod.yaml`) sets `ghcrPullSecret.enabled=true` and
   `global.imagePullSecrets[].name=ghcr-pull` — ESO materializes the secret and
   every Deployment + migration Job references it automatically.

> **Rejected alternative:** making the GHCR packages public would avoid the
> pull-secret machinery but exposes private images unconditionally. The
> credential-in-Vault approach was chosen to keep images private.

## Files here

```
prereqs/
  cert-manager-values.yaml      # jetstack/cert-manager overlay (installCRDs)
  ingress-nginx-values.yaml     # ingress-nginx/ingress-nginx overlay
  vault-values.yaml             # hashicorp/vault standalone (file storage) overlay
  eso-values.yaml               # external-secrets/external-secrets overlay (installCRDs)
  manifests/
    cluster-issuer.yaml         # Let's Encrypt staging + prod ClusterIssuers
    cluster-secret-store.yaml   # ESO ClusterSecretStore "vault-backend" (Vault k8s auth)
    vault-policy.hcl            # Vault read-only policy for secret/data/events/*
    vault-bootstrap.md          # Vault init/unseal + KV-v2 + policy + k8s auth role
```

## Validation (no cluster required)

```bash
# Render each prereq chart with its overlay (catches values/schema errors):
helm template cert-manager jetstack/cert-manager --version v1.19.5 \
  -f cert-manager-values.yaml | kubeconform -ignore-missing-schemas -summary
helm template ingress-nginx ingress-nginx/ingress-nginx --version 4.15.1 \
  -f ingress-nginx-values.yaml | kubeconform -ignore-missing-schemas -summary
helm template vault hashicorp/vault --version 0.30.1 \
  -f vault-values.yaml | kubeconform -ignore-missing-schemas -summary
helm template external-secrets external-secrets/external-secrets --version 0.10.7 \
  -f eso-values.yaml | kubeconform -ignore-missing-schemas -summary

# Validate the hand-written manifests (CRDs are skipped, structure is checked):
kubeconform -ignore-missing-schemas -summary \
  manifests/cluster-issuer.yaml manifests/cluster-secret-store.yaml
```
