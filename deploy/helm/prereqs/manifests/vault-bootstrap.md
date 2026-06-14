# Vault bootstrap — Kubernetes auth + policy for ESO

One-time wiring so the `vault-backend` ClusterSecretStore can read secrets. Run
these **after** the Vault Helm release is up and **initialized + unsealed**, and
**after** ESO is installed (its ServiceAccount must exist). All commands run
against the in-cluster Vault; the simplest way is an interactive shell in the
Vault pod (it ships the `vault` CLI):

```bash
kubectl -n vault exec -it vault-0 -- sh
export VAULT_ADDR=http://127.0.0.1:8200
vault login <root-or-admin-token>
```

> The same steps work against the local docker-compose `vault` profile — just
> `export VAULT_ADDR=http://127.0.0.1:8200` and `VAULT_TOKEN=<root>` on your host
> (dev mode auto-initializes + unseals, so skip the init/unseal section).

## 0. Initialize + unseal (in-cluster, file storage — NOT needed in dev mode)

```bash
vault operator init -key-shares=1 -key-threshold=1   # save Unseal Key + Root Token
vault operator unseal <unseal-key>
vault login <root-token>
```

## 1. Enable the KV-v2 secrets engine at `secret/`

```bash
# The dev-mode container enables this automatically; the file-storage server does not.
vault secrets enable -path=secret -version=2 kv || true
```

## 2. Write the read-only policy

`vault-policy.hcl` in this directory grants read on `secret/data/events/*`:

```bash
vault policy write events-read - <<'EOF'
path "secret/data/events/*" {
  capabilities = ["read"]
}
path "secret/metadata/events/*" {
  capabilities = ["read", "list"]
}
EOF
```

## 3. Enable + configure Kubernetes auth

```bash
vault auth enable kubernetes || true

# Inside the Vault pod the kube API + SA token are available at the usual paths.
vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  token_reviewer_jwt=@/var/run/secrets/kubernetes.io/serviceaccount/token
```

## 4. Bind the `events` role to the ServiceAccounts that read Vault

ESO authenticates as its own ServiceAccount (`external-secrets` in the
`external-secrets` namespace — that is what `cluster-secret-store.yaml`
references). Bind the role to it; also bind the platform namespace
ServiceAccounts so a future per-workload auth path keeps working.

```bash
vault write auth/kubernetes/role/events \
  bound_service_account_names="external-secrets,default" \
  bound_service_account_namespaces="external-secrets,events-platform" \
  policies="events-read" \
  ttl="1h"
```

After this the ClusterSecretStore reports `Valid` and each ExternalSecret
resolves `secret/data/events/<service>` into the `<release>-env` Secret.

## 5. Seed the secret values

Vault is now readable but empty. Populate it from the repo's dev defaults:

```bash
VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<root> \
  deploy/scripts/seed-vault.sh
```

See `deploy/scripts/seed-vault.sh` for the env-var → service mapping (sourced
from `.env.example`, the same keys the compose stack uses).
