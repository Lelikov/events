{{/*
events-common.hpa — gated by .Values.hpa.enabled. autoscaling/v2 HPA on
CPU + memory utilisation. min/max from values. When enabled the Deployment
omits a static replica count so the HPA owns scaling.
*/}}
{{- define "events-common.hpa" -}}
{{- if .Values.hpa.enabled -}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "events-common.fullname" . }}
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "events-common.fullname" . }}
  minReplicas: {{ .Values.hpa.minReplicas | default 2 }}
  maxReplicas: {{ .Values.hpa.maxReplicas | default 5 }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.hpa.targetCPUUtilizationPercentage | default 80 }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .Values.hpa.targetMemoryUtilizationPercentage | default 80 }}
{{- end -}}
{{- end -}}
