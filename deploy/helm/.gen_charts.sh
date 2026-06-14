#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# matrix: service|containerPort|ingress(on/off)|host|readiness|liveness|migration|deployCommand
# deployCommand: Deployment command override (DB-owning services bypass the
# entrypoint alembic step; migrations run via the Helm hook Job instead).
matrix=(
  "event-receiver|8888|on|receiver.example.com|/ready|/health|off|"
  "event-saver|8888|off||/ready|/health|on|[\"uvicorn\",\"event_saver.main:app\",\"--host\",\"0.0.0.0\",\"--port\",\"8888\",\"--log-config\",\"uvicorn_config.json\"]"
  "event-booking|8888|off||/ready|/health|off|"
  "event-admin|8888|on|admin-api.example.com|/ready|/health|off|"
  "event-admin-frontend|80|on|admin.example.com|/health|/health|off|"
  "event-users|8888|off||/ready|/health|on|[\"uvicorn\",\"event_users.main:app\",\"--host\",\"0.0.0.0\",\"--port\",\"8888\",\"--log-config\",\"uvicorn_config.json\"]"
  "event-notifier|8888|off||/ready|/health|on|[\"uvicorn\",\"event_notifier.main:app\",\"--host\",\"0.0.0.0\",\"--port\",\"8888\"]"
  "event-shortener|8888|on|s.example.com|/ready|/health|on|[\"uvicorn\",\"event_shortener.main:app\",\"--host\",\"0.0.0.0\",\"--port\",\"8888\",\"--log-config\",\"uvicorn_config.json\"]"
  "jitsi-chat|80|on|meet.example.com|/health|/health|off|"
)

for row in "${matrix[@]}"; do
  IFS='|' read -r svc port ing host readiness liveness migration deploycmd <<< "$row"
  dir="charts/$svc"
  cmd_line="command: []"
  if [ -n "$deploycmd" ]; then
    cmd_line="command: $deploycmd"
  fi

  cat > "$dir/Chart.yaml" <<EOF
apiVersion: v2
name: $svc
description: Thin per-service chart for $svc; renders resources via the events-common library.
type: application
version: 0.1.0
appVersion: "latest"
dependencies:
  - name: events-common
    version: 0.1.0
    repository: "file://../../library/events-common"
EOF

  # ingress block
  if [ "$ing" = "on" ]; then
    ingress_block=$(cat <<EOF
ingress:
  enabled: true
  className: nginx
  clusterIssuer: letsencrypt-prod
  host: $host
  path: /
  pathType: Prefix
  annotations: {}
  tls:
    enabled: true
    secretName: ""
EOF
)
  else
    ingress_block=$(cat <<EOF
ingress:
  enabled: false
  className: nginx
  clusterIssuer: letsencrypt-prod
  host: ""
  path: /
  pathType: Prefix
  annotations: {}
  tls:
    enabled: true
    secretName: ""
EOF
)
  fi

  # migration block + command override (DB-owning services bypass entrypoint migration)
  if [ "$migration" = "on" ]; then
    migration_block=$(cat <<EOF
migration:
  enabled: true
  backoffLimit: 3
  command: ["alembic", "upgrade", "head"]
EOF
)
  else
    migration_block=$(cat <<EOF
migration:
  enabled: false
  backoffLimit: 3
EOF
)
  fi

  cat > "$dir/values.yaml" <<EOF
# Values for $svc. Only k8s spec lives here — NEVER app config or secrets.
# All runtime env (secret and non-secret) comes from Vault via ESO (phase 2).

nameOverride: ""
fullnameOverride: ""

replicas: 1

image:
  repository: ghcr.io/lelikov/$svc
  tag: latest
  pullPolicy: IfNotPresent

containerPort: $port

# Optional command/args override for the Deployment (e.g. bypass entrypoint
# migration on DB-owning services so replicas don't migrate concurrently).
$cmd_line
args: []

probes:
  liveness: $liveness
  readiness: $readiness
  initialDelaySeconds: 5
  periodSeconds: 10

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

# ExternalSecret: maps Vault path -> k8s Secret consumed via envFrom.
# Target secret name is "<fullname>-env" (set by the library).
externalSecret:
  enabled: true
  refreshInterval: 1h
  storeKind: ClusterSecretStore
  storeName: vault-backend
  vaultPath: secret/data/events/$svc

$ingress_block

hpa:
  enabled: false
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80

$migration_block

podAnnotations: {}
readOnlyRootFilesystem: false
EOF

  # templates: one all.yaml that includes each named template, nested under
  # the subchart's values via the events-common alias. Because dependencies
  # share the parent context, templates reference the library includes directly.
  cat > "$dir/templates/all.yaml" <<'EOF'
{{- include "events-common.deployment" . }}
---
{{ include "events-common.service" . }}
{{- with (include "events-common.ingress" .) }}
---
{{ . }}
{{- end }}
{{- with (include "events-common.hpa" .) }}
---
{{ . }}
{{- end }}
{{- with (include "events-common.externalsecret" .) }}
---
{{ . }}
{{- end }}
{{- with (include "events-common.migrationJob" .) }}
---
{{ . }}
{{- end }}
EOF

done
echo "generated $(ls charts | wc -l | tr -d ' ') charts"
