{{/*
Expand the name of the chart.
*/}}
{{- define "creatorclip.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "creatorclip.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels for a given component.
*/}}
{{- define "creatorclip.selectorLabels" -}}
app.kubernetes.io/name: {{ include "creatorclip.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Image reference.
*/}}
{{- define "creatorclip.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end }}

{{/*
envFrom reference — loads all env vars from the named secret.
*/}}
{{- define "creatorclip.envFrom" -}}
envFrom:
  - secretRef:
      name: {{ .Values.envSecretName }}
{{- end }}
