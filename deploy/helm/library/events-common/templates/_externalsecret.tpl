{{/*
events-common.externalsecret — gated by .Values.externalSecret.enabled
(default true). Maps a Vault path to a k8s Secret via External Secrets
Operator. The target secret name MUST match events-common.envSecretName
so the Deployment + migration Job pick it up via envFrom.

Phase 2 provisions the ClusterSecretStore (default name "vault-backend")
with Vault Kubernetes auth and seeds the Vault paths.
*/}}
{{- define "events-common.externalsecret" -}}
{{- if .Values.externalSecret.enabled -}}
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: {{ include "events-common.fullname" . }}
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
spec:
  refreshInterval: {{ .Values.externalSecret.refreshInterval | default "1h" }}
  secretStoreRef:
    kind: {{ .Values.externalSecret.storeKind | default "ClusterSecretStore" }}
    name: {{ .Values.externalSecret.storeName | default "vault-backend" }}
  target:
    name: {{ include "events-common.envSecretName" . }}
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: {{ required "externalSecret.vaultPath is required" .Values.externalSecret.vaultPath }}
{{- end -}}
{{- end -}}
