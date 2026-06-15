{{/*
events-common helpers — standard Helm naming + app.kubernetes.io labels.
*/}}

{{/*
Expand the name of the chart. Prefers an explicit .Values.nameOverride,
otherwise the release's chart name (the per-service chart, not the library).
*/}}
{{- define "events-common.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Truncated at 63 chars (k8s DNS limit, minus room
for resource suffixes). Honours fullnameOverride.
*/}}
{{- define "events-common.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version for the helm.sh/chart label.
*/}}
{{- define "events-common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "events-common.labels" -}}
helm.sh/chart: {{ include "events-common.chart" . }}
{{ include "events-common.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: events-platform
{{- end -}}

{{/*
Selector labels — stable across upgrades, used by Service + Deployment selector.
*/}}
{{- define "events-common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "events-common.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Name of the k8s Secret that carries this service's env. This is the single
source of truth referenced by the Deployment's envFrom, the migration Job,
and the ExternalSecret target. Phase 2 (ESO) must populate THIS secret name.
*/}}
{{- define "events-common.envSecretName" -}}
{{- printf "%s-env" (include "events-common.fullname" .) -}}
{{- end -}}

{{/*
events-common.imagePullSecrets — renders an imagePullSecrets block from
.Values.global.imagePullSecrets (set once by the umbrella for all subcharts),
falling back to a per-chart .Values.imagePullSecrets. Emits nothing when unset.
Include with a pod-spec indent, e.g. `{{- include "events-common.imagePullSecrets" . | nindent 6 }}`.
*/}}
{{- define "events-common.imagePullSecrets" -}}
{{- $secrets := (default dict .Values.global).imagePullSecrets | default .Values.imagePullSecrets -}}
{{- with $secrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
