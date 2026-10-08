{{/* 이름·레이블 */}}
{{- define "agentnexus.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "agentnexus.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agentnexus
{{- end -}}

{{/* 사용법: include "agentnexus.selector" (list . "api") */}}
{{- define "agentnexus.selector" -}}
app.kubernetes.io/name: {{ (index . 0).Chart.Name }}
app.kubernetes.io/instance: {{ (index . 0).Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{/* 이미지 — global.imageRegistry 접두사(에어갭 사설 레지스트리) */}}
{{- define "agentnexus.image" -}}
{{- $root := index . 0 -}}{{- $img := index . 1 -}}
{{- if $root.Values.global.imageRegistry -}}{{ printf "%s/%s:%s" $root.Values.global.imageRegistry $img.repository (toString $img.tag) }}{{- else -}}{{ printf "%s:%s" $img.repository (toString $img.tag) }}{{- end -}}
{{- end -}}

{{- define "agentnexus.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "agentnexus.fullname" .)) -}}
{{- end -}}

{{- define "agentnexus.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}{{ .Values.serviceAccount.name | default (include "agentnexus.fullname" .) }}{{- else -}}{{ .Values.serviceAccount.name | default "default" }}{{- end -}}
{{- end -}}

{{- define "agentnexus.multiReplica" -}}
{{- if or .Values.autoscaling.enabled (gt (int .Values.api.replicas) 1) -}}true{{- end -}}
{{- end -}}

{{/* 공통 파드 보안·풀 시크릿 */}}
{{- define "agentnexus.podCommon" -}}
serviceAccountName: {{ include "agentnexus.serviceAccountName" . }}
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* 설정 검증 — 잘못된 조합은 설치 전에 거부 */}}
{{- define "agentnexus.validate" -}}
{{- if and (include "agentnexus.multiReplica" .) .Values.persistence.enabled (not .Values.persistence.existingClaim) (eq .Values.persistence.accessMode "ReadWriteOnce") -}}
{{- fail "api 복제본이 2개 이상(또는 autoscaling)이면 persistence.accessMode=ReadWriteMany 또는 persistence.existingClaim 이 필요합니다 (audit-archive 공유)." -}}
{{- end -}}
{{- if and (not .Values.postgresql.enabled) (not .Values.database.url) (not .Values.secrets.existingSecret) -}}
{{- fail "postgresql.enabled=false 이면 database.url 또는 secrets.existingSecret(DATABASE_URL 포함) 이 필요합니다." -}}
{{- end -}}
{{- if and (eq .Values.config.rateLimitBackend "redis") (not .Values.valkey.enabled) (not .Values.valkey.url) -}}
{{- fail "config.rateLimitBackend=redis 이면 valkey.enabled=true 또는 valkey.url 이 필요합니다." -}}
{{- end -}}
{{- if and (eq .Values.config.policyEngine "opa") (not .Values.opa.enabled) -}}
{{- fail "config.policyEngine=opa 이면 opa.enabled=true 가 필요합니다." -}}
{{- end -}}
{{- end -}}
