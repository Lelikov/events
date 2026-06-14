{{/*
events-common.migrationJob — gated by .Values.migration.enabled. A Helm
pre-install/pre-upgrade hook Job running alembic migrations for DB-owning
services. Uses the same image and the same ESO-managed env Secret as the
Deployment. App Deployments override command to skip the entrypoint
migration, so N replicas never migrate concurrently.
*/}}
{{- define "events-common.migrationJob" -}}
{{- if .Values.migration.enabled -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "events-common.fullname" . }}-migrate
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: {{ .Values.migration.backoffLimit | default 3 }}
  template:
    metadata:
      labels:
        {{- include "events-common.selectorLabels" . | nindent 8 }}
    spec:
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: {{ include "events-common.name" . }}-migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default "latest" }}"
          imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
          command: {{ .Values.migration.command | default (list "alembic" "upgrade" "head") | toJson }}
          envFrom:
            - secretRef:
                name: {{ include "events-common.envSecretName" . }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
{{- end -}}
{{- end -}}
