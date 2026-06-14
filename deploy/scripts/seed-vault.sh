#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# seed-vault.sh — populate Vault KV-v2 with each service's runtime env.
#
# Writes one KV-v2 secret per deployable service under secret/events/<service>,
# so the matching ExternalSecret (vaultPath: secret/data/events/<service>)
# resolves into the <release>-env Secret consumed by the Deployment via envFrom.
#
# SOURCE OF TRUTH: the repo's .env.example (the same keys the docker-compose
# stack uses). We source those dev defaults, then below map them to the exact
# env-var names each service expects (taken from docker-compose.yml's
# `environment:` blocks). Internal URLs use Kubernetes Service DNS
# (http://<service>:8888) instead of compose hostnames; DB DSNs / RabbitMQ URL
# are TODO placeholders because Postgres/RabbitMQ are EXTERNAL/managed in prod
# (the design doc: charts never create the databases).
#
# Idempotent: `vault kv put` overwrites the whole secret each run.
# Parameterized by VAULT_ADDR / VAULT_TOKEN (works against the in-cluster Vault
# or the local docker-compose `vault` profile).
#
# Usage:
#   VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<root> deploy/scripts/seed-vault.sh
#
# Requires the `vault` CLI on PATH, OR set USE_DOCKER_VAULT=1 to exec the CLI
# inside the compose `vault` container (no host install needed):
#   USE_DOCKER_VAULT=1 VAULT_TOKEN=<root> deploy/scripts/seed-vault.sh
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/../.env.example}"
# Resolve .env.example at repo root (scripts live in deploy/scripts/).
[ -f "$ENV_FILE" ] || ENV_FILE="$(cd "${REPO_ROOT}/.." && pwd)/.env.example"
[ -f "$ENV_FILE" ] || { echo "ERROR: .env.example not found at $ENV_FILE"; exit 1; }

: "${VAULT_ADDR:=http://127.0.0.1:8200}"
: "${VAULT_TOKEN:?Set VAULT_TOKEN (root or a token that can write secret/events/*)}"
export VAULT_ADDR VAULT_TOKEN

# Vault CLI wrapper: native binary, or exec inside the compose vault container.
USE_DOCKER_VAULT="${USE_DOCKER_VAULT:-0}"
VAULT_CONTAINER="${VAULT_CONTAINER:-vault}"
vault_cli() {
  if [ "$USE_DOCKER_VAULT" = "1" ]; then
    docker compose --profile vault exec -T \
      -e VAULT_ADDR="http://127.0.0.1:8200" -e VAULT_TOKEN="$VAULT_TOKEN" \
      "$VAULT_CONTAINER" vault "$@"
    return
  fi
  vault "$@"
}

# Load the dev defaults (KEY=VALUE lines; ignore comments/blanks). We parse
# line-by-line rather than `source`-ing the file: .env files use docker-compose
# semantics (unquoted values may contain spaces, e.g. "Events Dev"), which a
# shell `source` would mis-tokenize. Everything after the first '=' is the value.
echo "Loading dev defaults from: $ENV_FILE"
while IFS= read -r line; do
  case "$line" in
    ''|'#'*) continue ;;
    [A-Z_]*=*) ;;
    *) continue ;;
  esac
  key="${line%%=*}"
  val="${line#*=}"
  # Strip one layer of surrounding quotes if present.
  case "$val" in
    \"*\") val="${val#\"}"; val="${val%\"}" ;;
    \'*\') val="${val#\'}"; val="${val%\'}" ;;
  esac
  printf -v "$key" '%s' "$val"
  export "$key"
done < "$ENV_FILE"

# Internal service URLs (Kubernetes Service DNS; port 8888 internal everywhere).
RECEIVER_URL="http://event-receiver:8888"
USERS_URL="http://event-users:8888"
ADMIN_URL="http://event-admin:8888"
BOOKING_RECEIVER_BOOKING_URL="${RECEIVER_URL}/event/booking"
ADMIN_RECEIVER_ADMIN_URL="${RECEIVER_URL}/event/admin"
SHORTENER_INTERNAL_URL="http://event-shortener:8888"

# OpenTelemetry collector in-cluster endpoint (events-observability release).
# The collector Service name is produced by the opentelemetry-collector chart:
# <release>-opentelemetry-collector. All 7 Python services receive OTLP gRPC
# on port 4317 via this endpoint. Sampling: parentbased_traceidratio at 10 %.
OTEL_COLLECTOR_ENDPOINT="http://events-observability-opentelemetry-collector:4317"

# DB / broker connections are EXTERNAL in prod — operators set the real DSNs.
# These are overridable via the environment (`: "${VAR:=default}"`), so the kind
# smoke (smoke.sh) can point them at the in-cluster devDependencies before
# calling this script; left unset they keep the CHANGE-ME prod placeholders.
: "${PG_SAVER_DSN_PH:=postgresql+asyncpg://USER:PASSWORD@CHANGE-ME-saver-host:5432/event_saver}"
: "${PG_USERS_DSN_PH:=postgresql+asyncpg://USER:PASSWORD@CHANGE-ME-users-host:5432/event_users}"
: "${PG_NOTIFIER_DSN_PH:=postgresql+asyncpg://USER:PASSWORD@CHANGE-ME-notifier-host:5432/event_notifier}"
: "${PG_SHORTENER_DSN_PH:=postgresql+asyncpg://USER:PASSWORD@CHANGE-ME-shortener-host:5432/event_shortener}"
: "${CALCOM_DSN_PH:=postgresql+asyncpg://USER:PASSWORD@CHANGE-ME-calcom-host:5432/calcom}"
: "${RABBIT_URL_PH:=amqp://USER:PASSWORD@CHANGE-ME-rabbit-host:5672/}"

put() {
  local svc="$1"; shift
  echo "  -> secret/events/${svc}"
  vault_cli kv put "secret/events/${svc}" "$@" >/dev/null
}

echo "Seeding Vault at ${VAULT_ADDR} ..."

# --- event-receiver ---------------------------------------------------------
put event-receiver \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  RABBIT_URL="${RABBIT_URL_PH}" \
  CORS_ORIGINS="https://admin.example.com,https://meet.example.com" \
  AUTHORIZATION_JWT_VERIFY_KEY="${JITSI_JWT_SECRET}" \
  AUTHORIZATION_JWT_ALGORITHM="HS256" \
  AUTHORIZATION_JWT_ISSUER="${JITSI_JWT_ISS}" \
  AUTHORIZATION_JWT_AUDIENCE="${JITSI_JWT_AUD}" \
  EMAIL_API_KEY="${EMAIL_API_KEY}" \
  GETSTREAM_API_KEY="${CHAT_API_KEY}" \
  GETSTREAM_API_SECRET="${CHAT_API_SECRET}" \
  GETSTREAM_USER_ID_ENCRYPTION_KEY="${CHAT_USER_ID_ENCRYPTION_KEY}" \
  BOOKING_API_KEY="${BOOKING_API_KEY}" \
  ADMIN_API_KEY="${ADMIN_API_KEY}" \
  CALCOM_WEBHOOK_SECRET="${CALCOM_WEBHOOK_SECRET}" \
  EVENT_USERS_API_URL="${USERS_URL}" \
  EVENT_USERS_API_TOKEN="${USERS_API_BEARER_TOKEN}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-receiver" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-saver ------------------------------------------------------------
put event-saver \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  RABBIT_URL="${RABBIT_URL_PH}" \
  POSTGRES_DSN="${PG_SAVER_DSN_PH}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-saver" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-booking ----------------------------------------------------------
put event-booking \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  CALCOM_POSTGRES_DSN="${CALCOM_DSN_PH}" \
  RABBIT_URL="${RABBIT_URL_PH}" \
  EVENTS_ENDPOINT_URL="${BOOKING_RECEIVER_BOOKING_URL}" \
  EVENTS_API_KEY="${BOOKING_API_KEY}" \
  JITSI_JWT_SECRET="${JITSI_JWT_SECRET}" \
  JITSI_JWT_AUD="${JITSI_JWT_AUD}" \
  JITSI_JWT_ISS="${JITSI_JWT_ISS}" \
  JITSI_JWT_SUB="${JITSI_JWT_SUB}" \
  MEETING_HOST_URL="https://meet.example.com" \
  CHAT_API_KEY="${CHAT_API_KEY}" \
  CHAT_API_SECRET="${CHAT_API_SECRET}" \
  CHAT_USER_ID_ENCRYPTION_KEY="${CHAT_USER_ID_ENCRYPTION_KEY}" \
  CHAT_BASE_URL="${CHAT_BASE_URL}" \
  SHORTENER_URL="${SHORTENER_INTERNAL_URL}" \
  SHORTENER_API_KEY="${SHORTENER_API_KEY}" \
  IS_ENABLE_BOOKING_CONSTRAINTS="${IS_ENABLE_BOOKING_CONSTRAINTS}" \
  BLACKLIST_ENABLED="${BLACKLIST_ENABLED}" \
  EVENT_ADMIN_API_URL="${ADMIN_URL}" \
  BLACKLIST_SERVICE_TOKEN="${BLACKLIST_SERVICE_TOKEN}" \
  BLACKLIST_CACHE_TTL="${BLACKLIST_CACHE_TTL}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-booking" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-users ------------------------------------------------------------
put event-users \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  POSTGRES_DSN="${PG_USERS_DSN_PH}" \
  JWT_SECRET_KEY="${ADMIN_JWT_SECRET}" \
  API_BEARER_TOKEN="${USERS_API_BEARER_TOKEN}" \
  CORS_ORIGINS='["https://admin.example.com"]' \
  RABBIT_URL="${RABBIT_URL_PH}" \
  IS_CONSUMER_ENABLED="true" \
  IS_SYNC_ENABLED="false" \
  IS_WEBHOOK_ENABLED="false" \
  CRM_API_URL="${CRM_API_URL}" \
  CRM_API_TOKEN="${CRM_API_TOKEN}" \
  CRM_ENCRYPTION_KEY="${CRM_ENCRYPTION_KEY}" \
  EVENT_ADMIN_URL="${ADMIN_URL}" \
  EVENT_ADMIN_CACHE_TOKEN="${CACHE_INVALIDATION_TOKEN}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-users" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-admin ------------------------------------------------------------
put event-admin \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  POSTGRES_DSN="${PG_SAVER_DSN_PH}" \
  JWT_SECRET_KEY="${ADMIN_JWT_SECRET}" \
  CORS_ORIGINS='["https://admin.example.com"]' \
  USERS_SERVICE_URL="${USERS_URL}" \
  USERS_SERVICE_API_TOKEN="${USERS_API_BEARER_TOKEN}" \
  CACHE_INVALIDATION_TOKEN="${CACHE_INVALIDATION_TOKEN}" \
  EVENT_RECEIVER_URL="${RECEIVER_URL}" \
  EVENT_RECEIVER_API_KEY="${ADMIN_API_KEY}" \
  BLACKLIST_SERVICE_TOKEN="${BLACKLIST_SERVICE_TOKEN}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-admin" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-notifier ---------------------------------------------------------
put event-notifier \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  RABBIT_URL="${RABBIT_URL_PH}" \
  DATABASE_URL="${PG_NOTIFIER_DSN_PH}" \
  EVENT_USERS_URL="${USERS_URL}" \
  EVENT_USERS_TOKEN="${USERS_API_BEARER_TOKEN}" \
  EVENTS_ENDPOINT_URL="${ADMIN_RECEIVER_ADMIN_URL}" \
  EVENTS_API_KEY="${ADMIN_API_KEY}" \
  DEFAULT_LOCALE="ru" \
  UNISENDER_BASE_URL="${UNISENDER_BASE_URL}" \
  UNISENDER_API_KEY="${UNISENDER_API_KEY}" \
  UNISENDER_FROM_EMAIL="${UNISENDER_FROM_EMAIL}" \
  UNISENDER_FROM_NAME="${UNISENDER_FROM_NAME}" \
  UNISENDER_TEMPLATE_IDS='{"ru":{"BOOKING_CREATED":"00000000-0000-4000-8000-000000000001","BOOKING_CANCELLED":"00000000-0000-4000-8000-000000000002","BOOKING_RESCHEDULED":"00000000-0000-4000-8000-000000000003","BOOKING_REASSIGNED":"00000000-0000-4000-8000-000000000004","BOOKING_REMINDER":"00000000-0000-4000-8000-000000000005","BOOKING_REJECTED":"00000000-0000-4000-8000-000000000006","BOOKING_REJECTED_BLACKLISTED":"00000000-0000-4000-8000-000000000007"},"en":{"BOOKING_REJECTED_BLACKLISTED":"00000000-0000-4000-8000-000000000008"}}' \
  TELEGRAM_BASE_URL="${TELEGRAM_BASE_URL}" \
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-notifier" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-shortener --------------------------------------------------------
put event-shortener \
  DEBUG="false" \
  LOG_LEVEL="${LOG_LEVEL}" \
  POSTGRES_DSN="${PG_SHORTENER_DSN_PH}" \
  SHORTENER_API_KEY="${SHORTENER_API_KEY}" \
  OTEL_SDK_DISABLED="false" \
  OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_COLLECTOR_ENDPOINT}" \
  OTEL_SERVICE_NAME="event-shortener" \
  OTEL_TRACES_SAMPLER="parentbased_traceidratio" \
  OTEL_TRACES_SAMPLER_ARG="0.1"

# --- event-admin-frontend (nginx SPA; same-origin proxy, no app secrets) ----
# VITE_SENTRY_DSN: leave empty — the operator sets the real DSN for this project.
# VITE_SENTRY_BACKEND_URL: same-origin (nginx proxy) — window.location.origin
#   already covers it; tracePropagationTargets needs no extra entry.
put event-admin-frontend \
  VITE_API_BASE_URL="" \
  VITE_SENTRY_ENABLED="true" \
  VITE_SENTRY_DSN="" \
  VITE_SENTRY_ENVIRONMENT="production" \
  VITE_SENTRY_TRACES_SAMPLE_RATE="0.1"

# --- jitsi-chat (browser SPA; only public VITE_* vars) ----------------------
# VITE_SENTRY_DSN: leave empty — the operator sets the real DSN for this project.
# VITE_SENTRY_BACKEND_URL: the event-receiver public origin that jitsi-chat calls.
#   Set to the deployed receiver URL (e.g. https://receiver.example.com) so
#   Sentry's browserTracingIntegration adds sentry-trace headers to those fetches.
put jitsi-chat \
  VITE_JITSI_DOMAIN="${VITE_JITSI_DOMAIN}" \
  VITE_WEBHOOK_URL="https://receiver.example.com/event/jitsi" \
  VITE_STREAM_CHAT_API_KEY="${CHAT_API_KEY}" \
  VITE_STREAM_CHAT_BASE_URL="${VITE_STREAM_CHAT_BASE_URL}" \
  VITE_SENTRY_ENABLED="true" \
  VITE_SENTRY_DSN="" \
  VITE_SENTRY_ENVIRONMENT="production" \
  VITE_SENTRY_TRACES_SAMPLE_RATE="0.1" \
  VITE_SENTRY_BACKEND_URL=""

echo "Done. Seeded 9 services under secret/events/*."
echo "NOTE: VITE_SENTRY_DSN is empty in event-admin-frontend and jitsi-chat — set the real DSN"
echo "  in Vault after creating a Sentry project (VITE_SENTRY_ENABLED=true gates on a non-empty DSN)."
echo "NOTE: VITE_SENTRY_BACKEND_URL is empty in jitsi-chat — set to the deployed event-receiver"
echo "  origin (e.g. https://receiver.example.com) to enable sentry-trace propagation."
echo "Verify e.g.: VAULT_ADDR=${VAULT_ADDR} vault kv get secret/events/event-saver"
