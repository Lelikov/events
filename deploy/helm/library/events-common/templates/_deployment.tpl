{{/*
events-common.deployment — Deployment for a stateless service.

Env comes exclusively from the ESO-managed Secret (envFrom: secretRef);
no value-bearing ConfigMap. Optional command/args override lets DB-owning
services bypass the entrypoint's migration step (migrations run via the Job).
*/}}
{{- define "events-common.deployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "events-common.fullname" . }}
  labels:
    {{- include "events-common.labels" . | nindent 4 }}
spec:
{{- if not .Values.hpa.enabled }}
  replicas: {{ .Values.replicas | default 1 }}
{{- end }}
  selector:
    matchLabels:
      {{- include "events-common.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "events-common.labels" . | nindent 8 }}
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      securityContext:
        {{- if .Values.podSecurityContext }}
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
        {{- else }}
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
        {{- end }}
      containers:
        - name: {{ include "events-common.name" . }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default "latest" }}"
          imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
          {{- with .Values.command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.args }}
          args:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - name: http
              containerPort: {{ .Values.containerPort }}
              protocol: TCP
          envFrom:
            - secretRef:
                name: {{ include "events-common.envSecretName" . }}
          livenessProbe:
            httpGet:
              path: {{ .Values.probes.liveness | default "/health" }}
              port: http
            initialDelaySeconds: {{ .Values.probes.initialDelaySeconds | default 10 }}
            periodSeconds: {{ .Values.probes.periodSeconds | default 10 }}
          readinessProbe:
            httpGet:
              path: {{ .Values.probes.readiness | default "/ready" }}
              port: http
            initialDelaySeconds: {{ .Values.probes.initialDelaySeconds | default 5 }}
            periodSeconds: {{ .Values.probes.periodSeconds | default 10 }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          securityContext:
            allowPrivilegeEscalation: {{ .Values.allowPrivilegeEscalation | default false }}
            readOnlyRootFilesystem: {{ .Values.readOnlyRootFilesystem | default false }}
            capabilities:
              drop:
                - ALL
              {{- with .Values.capabilitiesAdd }}
              add:
                {{- toYaml . | nindent 16 }}
              {{- end }}
{{- end -}}
