{{/*
events-common.service — ClusterIP Service exposing the container port.
*/}}
{{- define "events-common.service" -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "events-common.fullname" . }}
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "events-common.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: {{ .Values.containerPort }}
      targetPort: http
      protocol: TCP
{{- end -}}
