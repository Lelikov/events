{{/*
events-common.ingress — gated by .Values.ingress.enabled. Standard
networking.k8s.io/v1 Ingress (CRD-free). TLS issued by cert-manager via
the cluster-issuer annotation; the per-host TLS secret is created by
cert-manager (phase 2 wires the ClusterIssuer + ingress-nginx).
*/}}
{{- define "events-common.ingress" -}}
{{- if .Values.ingress.enabled -}}
{{- $fullname := include "events-common.fullname" . -}}
{{- $svcPort := .Values.containerPort -}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ $fullname }}
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
  annotations:
    {{- if .Values.ingress.clusterIssuer }}
    cert-manager.io/cluster-issuer: {{ .Values.ingress.clusterIssuer | quote }}
    {{- end }}
    {{- with .Values.ingress.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
spec:
  {{- with .Values.ingress.className }}
  ingressClassName: {{ . }}
  {{- end }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts:
        - {{ .Values.ingress.host | quote }}
      secretName: {{ .Values.ingress.tls.secretName | default (printf "%s-tls" $fullname) }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          - path: {{ .Values.ingress.path | default "/" }}
            pathType: {{ .Values.ingress.pathType | default "Prefix" }}
            backend:
              service:
                name: {{ $fullname }}
                port:
                  number: {{ $svcPort }}
{{- end -}}
{{- end -}}
