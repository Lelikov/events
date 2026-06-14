#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# smoke.sh — end-to-end kind smoke for the events platform.
#
# Spins up a kind cluster, installs the prereqs (cert-manager, ingress-nginx,
# Vault in DEV mode, ESO), seeds Vault (DSN/RABBIT_URL pointed at the in-cluster
# Bitnami Postgres/RabbitMQ that values-kind.yaml's devDependencies bring up),
# wires a token-auth ClusterSecretStore, `helm install`s events-platform with
# values-kind.yaml, waits for all Deployments to be Available, then POSTs a real
# cal.com BOOKING_CREATED webhook (HMAC-signed) at event-receiver and asserts a
# 202. Tears the cluster down on exit (trap).
#
# DEV-MODE Vault: auto-initialized + unsealed (root token "root"), so we skip the
# init/unseal + kubernetes-auth bootstrap and use simple TOKEN auth for ESO.
# Production uses file-storage Vault + Kubernetes auth (see prereqs/).
#
# All waits are bounded; the script reports how far it got and never hangs.
#
# Usage:  deploy/scripts/smoke.sh
# Knobs:  KIND_CLUSTER (events-smoke), NS (events-platform), KEEP=1 (skip teardown)
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPTS_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
HELM_DIR="${DEPLOY_DIR}/helm"
PLATFORM="${HELM_DIR}/umbrella/events-platform"

KIND_CLUSTER="${KIND_CLUSTER:-events-smoke}"
NS="${NS:-events-platform}"
VAULT_NS="vault"
ESO_NS="external-secrets"
VAULT_ROOT_TOKEN="root"
KEEP="${KEEP:-0}"

# Pinned prereq chart versions (match deploy/helm/prereqs/README.md).
CERT_MANAGER_VER="v1.19.5"
INGRESS_VER="4.15.1"
VAULT_VER="0.30.1"
ESO_VER="0.10.7"

# --- result matrix (printed at the end) ------------------------------------
# Plain vars (no associative arrays — macOS ships bash 3.2).
R_cluster="not-reached"
R_prereqs="not-reached"
R_vault_seeded="not-reached"
R_platform_installed="not-reached"
R_pods_ready="not-reached"
R_receiver_202="not-reached"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[err]\033[0m %s\n' "$*"; }

need() { command -v "$1" >/dev/null 2>&1 || { err "missing required tool: $1"; MISSING=1; }; }

report() {
  log "SMOKE RESULT MATRIX"
  printf '  %-20s %s\n' "cluster_created:"     "${R_cluster}"
  printf '  %-20s %s\n' "prereqs_installed:"   "${R_prereqs}"
  printf '  %-20s %s\n' "vault_seeded:"        "${R_vault_seeded}"
  printf '  %-20s %s\n' "platform_installed:"  "${R_platform_installed}"
  printf '  %-20s %s\n' "pods_ready:"          "${R_pods_ready}"
  printf '  %-20s %s\n' "receiver_202:"        "${R_receiver_202}"
}

cleanup() {
  local rc=$?
  report
  if [ "$KEEP" = "1" ]; then
    warn "KEEP=1 set — leaving kind cluster '${KIND_CLUSTER}' running."
  else
    log "Tearing down kind cluster '${KIND_CLUSTER}'"
    kind delete cluster --name "${KIND_CLUSTER}" >/dev/null 2>&1 || true
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
MISSING=0
for t in kind kubectl helm docker; do need "$t"; done
[ "$MISSING" = "1" ] && { err "install the missing tools and retry"; exit 1; }
docker info >/dev/null 2>&1 || { err "Docker daemon not running"; exit 1; }

# 1. kind cluster -----------------------------------------------------------
log "Creating kind cluster '${KIND_CLUSTER}'"
if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
  warn "cluster already exists — reusing"
else
  kind create cluster --name "${KIND_CLUSTER}" --wait 120s || { err "kind create failed"; exit 1; }
fi
kubectl cluster-info --context "kind-${KIND_CLUSTER}" >/dev/null 2>&1 || { err "cluster unreachable"; exit 1; }
R_cluster="PASS"; ok "cluster up"

# 2. prereqs ----------------------------------------------------------------
log "Adding helm repos"
# NB: cert-manager is pulled from its OCI registry below (the jetstack HTTP
# index throttles and can hang `helm repo add` itself), so no jetstack repo here.
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo add hashicorp https://helm.releases.hashicorp.com >/dev/null 2>&1 || true
helm repo add external-secrets https://charts.external-secrets.io >/dev/null 2>&1 || true
helm repo update ingress-nginx hashicorp external-secrets >/dev/null 2>&1 || true

# helm's client-side `--wait` readiness watcher hangs indefinitely against this
# kind cluster (the informer never reports Ready even after pods are Running),
# so we install WITHOUT `--wait` and gate readiness with bounded
# `kubectl rollout status`, which polls the API server directly and returns.
roll() {  # roll <ns> <kind/name> ...  (each bounded; non-fatal warnings)
  local ns="$1"; shift
  local res
  for res in "$@"; do
    kubectl -n "$ns" rollout status "$res" --timeout=180s 2>/dev/null \
      || warn "$res in $ns not Ready within timeout"
  done
}

log "Installing cert-manager (OCI registry — the jetstack HTTP index throttles)"
helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager --version "${CERT_MANAGER_VER}" \
  --namespace cert-manager --create-namespace --set installCRDs=true --timeout 5m \
  || { err "cert-manager install failed"; exit 1; }
roll cert-manager deploy/cert-manager deploy/cert-manager-cainjector deploy/cert-manager-webhook

log "Installing ingress-nginx"
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx --version "${INGRESS_VER}" \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=ClusterIP --timeout 5m \
  || warn "ingress-nginx install returned non-zero (smoke port-forwards the receiver Service directly, so this is non-fatal)"
roll ingress-nginx deploy/ingress-nginx-controller

log "Installing Vault (dev mode — auto unseal, root token '${VAULT_ROOT_TOKEN}')"
helm upgrade --install vault hashicorp/vault --version "${VAULT_VER}" \
  --namespace "${VAULT_NS}" --create-namespace \
  --set "server.dev.enabled=true" \
  --set "server.dev.devRootToken=${VAULT_ROOT_TOKEN}" \
  --set "injector.enabled=false" --timeout 5m \
  || { err "vault install failed"; exit 1; }
kubectl -n "${VAULT_NS}" rollout status statefulset/vault --timeout=180s 2>/dev/null \
  || kubectl -n "${VAULT_NS}" wait --for=condition=Ready pod/vault-0 --timeout=180s || true

log "Installing External Secrets Operator"
helm upgrade --install external-secrets external-secrets/external-secrets --version "${ESO_VER}" \
  --namespace "${ESO_NS}" --create-namespace --set installCRDs=true --timeout 5m \
  || { err "ESO install failed"; exit 1; }
roll "${ESO_NS}" deploy/external-secrets deploy/external-secrets-webhook deploy/external-secrets-cert-controller
R_prereqs="PASS"; ok "prereqs installed"

# 3. seed Vault -------------------------------------------------------------
# Point service DSNs/RABBIT_URL at the in-cluster devDependencies (Bitnami
# Postgres/RabbitMQ that values-kind.yaml brings up under the events-platform
# release in namespace ${NS}). The seed script reads override env vars for the
# DSN/RABBIT placeholders.
PG_HOST="events-platform-devpostgresql.${NS}.svc"
RABBIT_HOST="events-platform-devrabbitmq.${NS}.svc"
export PG_SAVER_DSN_PH="postgresql+asyncpg://postgres:postgres@${PG_HOST}:5432/event_saver"
export PG_USERS_DSN_PH="postgresql+asyncpg://postgres:postgres@${PG_HOST}:5432/event_users"
export PG_NOTIFIER_DSN_PH="postgresql+asyncpg://postgres:postgres@${PG_HOST}:5432/event_notifier"
export PG_SHORTENER_DSN_PH="postgresql+asyncpg://postgres:postgres@${PG_HOST}:5432/event_shortener"
export CALCOM_DSN_PH="postgresql+asyncpg://postgres:postgres@${PG_HOST}:5432/calcom"
export RABBIT_URL_PH="amqp://guest:guest@${RABBIT_HOST}:5672/"

log "Port-forwarding Vault and seeding secrets"
kubectl -n "${VAULT_NS}" port-forward svc/vault 8200:8200 >/tmp/vault-pf.log 2>&1 &
VAULT_PF_PID=$!
# wait for the forward
for _ in $(seq 1 20); do curl -sf http://127.0.0.1:8200/v1/sys/health >/dev/null 2>&1 && break; sleep 1; done
if VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN="${VAULT_ROOT_TOKEN}" \
     USE_DOCKER_VAULT=0 "${SCRIPTS_DIR}/seed-vault.sh"; then
  R_vault_seeded="PASS"; ok "Vault seeded"
else
  R_vault_seeded="FAIL"; err "Vault seeding failed"; exit 1
fi
kill "${VAULT_PF_PID}" 2>/dev/null || true

# ClusterSecretStore (dev): TOKEN auth against the dev Vault (root token in a
# Secret). Production uses Kubernetes auth — see prereqs/manifests/.
log "Creating namespace + token-auth ClusterSecretStore"
kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "${ESO_NS}" create secret generic vault-token \
  --from-literal=token="${VAULT_ROOT_TOKEN}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: vault-backend
spec:
  provider:
    vault:
      server: "http://vault.${VAULT_NS}.svc:8200"
      path: "secret"
      version: "v2"
      auth:
        tokenSecretRef:
          name: vault-token
          namespace: ${ESO_NS}
          key: token
EOF
ok "ClusterSecretStore applied"

# 4. deploy the platform ----------------------------------------------------
log "helm dependency build (pull devDependencies + service charts)"
helm dependency build "${PLATFORM}" >/dev/null 2>&1 || helm dependency update "${PLATFORM}" >/dev/null 2>&1 || true

log "Installing events-platform (values-kind.yaml, devDependencies on)"
if helm upgrade --install events-platform "${PLATFORM}" \
     -n "${NS}" --create-namespace -f "${PLATFORM}/values-kind.yaml" \
     --timeout 6m; then
  R_platform_installed="PASS"; ok "platform installed"
else
  R_platform_installed="PARTIAL"; warn "helm install returned non-zero (pods may still be settling); continuing"
fi

# 5. wait for Deployments ---------------------------------------------------
log "Waiting for all Deployments to be Available (bounded 5m)"
if kubectl -n "${NS}" wait --for=condition=Available deploy --all --timeout=300s; then
  R_pods_ready="PASS"; ok "all Deployments Available"
else
  R_pods_ready="PARTIAL"
  warn "not all Deployments became Available — current state:"
  kubectl -n "${NS}" get pods -o wide 2>/dev/null | head -40 || true
fi

# 6. POST a real cal.com BOOKING_CREATED with HMAC --> expect 202 -----------
log "Sending HMAC-signed cal.com BOOKING_CREATED to event-receiver"
RECEIVER_SECRET="$(grep -E '^CALCOM_WEBHOOK_SECRET=' "${REPO_ROOT}/.env.example" | head -1 | cut -d= -f2-)"
RECEIVER_SECRET="${RECEIVER_SECRET:-dev-calcom-webhook-9d2c4f7a1e6b8350}"

# Extract the first BOOKING_CREATED object from event-booking/requests.jsonl
# (python-repr; literal_eval handles None/True/False), re-serialize as JSON.
BODY_FILE="$(mktemp)"
python3 - "${REPO_ROOT}/event-booking/requests.jsonl" "${BODY_FILE}" <<'PY'
import ast, json, sys
raw = open(sys.argv[1]).read()
objs, depth, start = [], 0, None
for i, c in enumerate(raw):
    if c == '{':
        if depth == 0: start = i
        depth += 1
    elif c == '}':
        depth -= 1
        if depth == 0: objs.append(raw[start:i+1])
created = None
for o in objs:
    d = ast.literal_eval(o)
    if d.get('triggerEvent') == 'BOOKING_CREATED':
        created = d; break
if created is None:
    sys.exit("no BOOKING_CREATED object in requests.jsonl")
open(sys.argv[2], 'w').write(json.dumps(created, separators=(',', ':')))
PY
SIG="$(python3 -c "import hmac,hashlib,sys;print(hmac.new(sys.argv[1].encode(),open(sys.argv[2],'rb').read(),hashlib.sha256).hexdigest())" "${RECEIVER_SECRET}" "${BODY_FILE}")"

kubectl -n "${NS}" port-forward svc/event-receiver 18888:8888 >/tmp/receiver-pf.log 2>&1 &
RECV_PF_PID=$!
for _ in $(seq 1 20); do curl -sf http://127.0.0.1:18888/health >/dev/null 2>&1 && break; sleep 1; done

HTTP_CODE="$(curl -s -o /tmp/receiver-resp.txt -w '%{http_code}' \
  -X POST http://127.0.0.1:18888/event/calcom \
  -H 'Content-Type: application/json' \
  -H "X-Cal-Signature-256: ${SIG}" \
  --data-binary "@${BODY_FILE}" || echo "000")"
kill "${RECV_PF_PID}" 2>/dev/null || true

echo "  receiver responded HTTP ${HTTP_CODE} (body: $(head -c 200 /tmp/receiver-resp.txt 2>/dev/null))"
if [ "${HTTP_CODE}" = "202" ]; then
  R_receiver_202="PASS"; ok "receiver returned 202 Accepted"
else
  R_receiver_202="FAIL (got ${HTTP_CODE})"; warn "receiver did not return 202"
fi

# cleanup() runs on exit (report + teardown).
