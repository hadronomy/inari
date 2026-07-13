{{/* Chart name, constrained to Kubernetes' DNS label limit. */}}
{{- define "inari.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Release-scoped resource name. */}}
{{- define "inari.fullname" -}}
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
{{- end }}

{{/* Chart identifier used by Helm's recommended labels. */}}
{{- define "inari.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Labels shared by every release-owned object. */}}
{{- define "inari.labels" -}}
helm.sh/chart: {{ include "inari.chart" . }}
app.kubernetes.io/name: {{ include "inari.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: inari
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* Stable labels safe to use in selectors. */}}
{{- define "inari.selectorLabels" -}}
app.kubernetes.io/name: {{ include "inari.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Stable selector labels for one workload component. */}}
{{- define "inari.componentSelectorLabels" -}}
{{ include "inari.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* The ServiceAccount name, with invalid external configurations rejected early. */}}
{{- define "inari.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "inari.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when serviceAccount.create is false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{/* An immutable image digest wins over a tag when both are supplied. */}}
{{- define "inari.image" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository (default .defaultTag .tag) -}}
{{- end -}}
{{- end }}

{{/* Controller image reference. */}}
{{- define "inari.controllerImage" -}}
{{- include "inari.image" (dict "repository" .Values.image.repository "tag" .Values.image.tag "digest" .Values.image.digest "defaultTag" .Chart.AppVersion) -}}
{{- end }}

{{/* Zenoh image reference. */}}
{{- define "inari.zenohImage" -}}
{{- include "inari.image" (dict "repository" .Values.zenoh.image.repository "tag" .Values.zenoh.image.tag "digest" .Values.zenoh.image.digest "defaultTag" .Chart.AppVersion) -}}
{{- end }}

{{/* Helm-test image reference. */}}
{{- define "inari.testImage" -}}
{{- include "inari.image" (dict "repository" .Values.tests.image.repository "tag" .Values.tests.image.tag "digest" .Values.tests.image.digest "defaultTag" "1.37.0") -}}
{{- end }}

{{/* Name of the Zenoh configuration ConfigMap. */}}
{{- define "inari.zenohConfigMapName" -}}
{{- default (printf "%s-zenoh" (include "inari.fullname" .)) .Values.zenoh.config.existingConfigMap -}}
{{- end }}

{{/* Stable, release-specific prefix for router IDs. */}}
{{- define "inari.zenohIdPrefix" -}}
{{- printf "%s/%s" .Release.Namespace .Release.Name | sha256sum | trunc 30 -}}
{{- end }}

{{/* Name of the migration Job in Helm-hook and declarative modes. */}}
{{- define "inari.migrationJobName" -}}
{{- if .Values.migrations.helmHook -}}
{{- printf "%s-migrate" (include "inari.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-migrate-%s" (include "inari.fullname" .) (.Chart.Version | replace "+" "-") | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{/* Cross-field invariants JSON Schema cannot express portably. */}}
{{- define "inari.validateValues" -}}
{{- if gt (int .Values.database.minConnections) (int .Values.database.maxConnections) -}}
{{- fail "database.minConnections must not exceed database.maxConnections" -}}
{{- end -}}
{{- if and .Values.controller.autoscaling.enabled (gt (int .Values.controller.autoscaling.minReplicas) (int .Values.controller.autoscaling.maxReplicas)) -}}
{{- fail "controller.autoscaling.minReplicas must not exceed maxReplicas" -}}
{{- end -}}
{{- if and .Values.managedGateway.enabled (not .Values.zenoh.enabled) -}}
{{- fail "zenoh.enabled must be true when managedGateway.enabled is true" -}}
{{- end -}}
{{- if and .Values.managedGateway.enabled (ne .Values.managedGateway.certificate.mode "step_ca") -}}
{{- fail "managedGateway.certificate.mode must be step_ca for a production managed gateway" -}}
{{- end -}}
{{- if and (ne .Values.zenoh.service.externalTrafficPolicy "") (eq .Values.zenoh.service.type "ClusterIP") -}}
{{- fail "zenoh.service.externalTrafficPolicy is valid only for NodePort or LoadBalancer services" -}}
{{- end -}}
{{- if and .Values.zenoh.service.loadBalancerSourceRanges (ne .Values.zenoh.service.type "LoadBalancer") -}}
{{- fail "zenoh.service.loadBalancerSourceRanges requires zenoh.service.type=LoadBalancer" -}}
{{- end -}}
{{- if and .Values.zenoh.enabled (not .Values.zenoh.config.existingConfigMap) (not .Values.zenoh.config.accessControl.enabled) -}}
{{- fail "generated Zenoh configuration requires accessControl.enabled=true" -}}
{{- end -}}
{{- if and .Values.zenoh.enabled (not .Values.zenoh.config.existingConfigMap) (ne .Values.zenoh.config.accessControl.defaultPermission "deny") -}}
{{- fail "generated Zenoh configuration requires accessControl.defaultPermission=deny" -}}
{{- end -}}
{{- end }}
