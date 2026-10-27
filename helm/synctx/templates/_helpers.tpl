{{/*
Expand the name of the chart.
*/}}
{{- define "synctx.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "synctx.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
서비스 값 맵의 enabled가 명시적으로 false가 아니면 "true"를 낸다. enabled 키가 없던 이전 릴리스 값으로
--reuse-values 업그레이드해도 서비스가 사라지지 않게 키가 없거나 null이면 켜진 것으로 본다.
sprig default는 false도 빈 값으로 보므로 쓰지 않는다.
*/}}
{{- define "synctx.isEnabled" -}}
{{- if ne (toString .enabled) "false" }}true{{- end }}
{{- end }}

{{/*
Sync 메트릭을 Pushgateway로 보낼 때 "true". 누적값을 sync-state PVC에 두므로 PVC 없이 메트릭을 켜면 synctx.validate가 렌더를 멈춘다.
*/}}
{{- define "synctx.syncMetricsPush" -}}
{{- if and .Values.metrics.enabled (include "synctx.isEnabled" .Values.sync) .Values.sync.persistence.enabled }}true{{- end }}
{{- end }}

{{/*
Feedback 메트릭을 Pushgateway로 보낼 때 "true". 누적값을 Feedback이 함께 쓰는 sync-state PVC에 두므로 PVC 없이 메트릭을 켜면 synctx.validate가 렌더를 멈춘다.
*/}}
{{- define "synctx.feedbackMetricsPush" -}}
{{- if and .Values.metrics.enabled .Values.feedback.enabled .Values.sync.persistence.enabled }}true{{- end }}
{{- end }}

{{/*
CronJob 메트릭을 받는 Pushgateway를 배포할 때 "true".
*/}}
{{- define "synctx.pushgateway" -}}
{{- if or (include "synctx.syncMetricsPush" .) (include "synctx.feedbackMetricsPush" .) }}true{{- end }}
{{- end }}

{{/*
켜진 서비스를 정해진 순서대로 쉼표로 잇는다. Viewer의 SYNCTX_ENABLED_SERVICES 값이다.
*/}}
{{- define "synctx.enabledServices" -}}
{{- $enabled := list }}
{{- range $name := list "viewer" "cdn" "marp" "slides" "drawio" "sync" }}
{{- if include "synctx.isEnabled" (index $.Values $name) }}
{{- $enabled = append $enabled $name }}
{{- end }}
{{- end }}
{{- if .Values.feedback.enabled }}
{{- $enabled = append $enabled "feedback" }}
{{- end }}
{{- join "," $enabled }}
{{- end }}

{{/*
서비스 옵션 의존성 검사. 차트가 .synctx.yaml을 만들지 않으면(synctxConfig.create=false) CDN 주소는 Viewer 시작 검사가 맡는다.
*/}}
{{- define "synctx.validate" -}}
{{- $render := .Values.viewer.renderTokenSecret | default dict }}
{{- if and $render.create $render.existingSecret }}
{{- fail "viewer.renderTokenSecret.create와 existingSecret은 함께 지정할 수 없습니다" }}
{{- end }}
{{- if and $render.create (not $render.current) }}
{{- fail "viewer.renderTokenSecret.create=true이면 current가 필요합니다" }}
{{- end }}
{{- if and $render.previous (not $render.current) }}
{{- fail "viewer.renderTokenSecret.previous만 지정할 수 없습니다. current가 필요합니다" }}
{{- end }}
{{- if .Values.synctxConfig.create }}
{{- $cfg := fromYaml (.Values.synctxConfig.data | default "") }}
{{- $viewer := get $cfg "viewer" | default dict }}
{{- if hasKey $viewer "render_token_ttl_seconds" }}
{{- $ttl := $viewer.render_token_ttl_seconds }}
{{- if not (and (or (kindIs "float64" $ttl) (kindIs "int" $ttl) (kindIs "int64" $ttl)) (regexMatch "^[0-9]+$" (toString $ttl)) (gt (float64 $ttl) 0.0)) }}
{{- fail "viewer.render_token_ttl_seconds는 양의 정수여야 합니다" }}
{{- end }}
{{- end }}
{{- range $repo := $cfg.repositories }}
{{- $embed := $repo.html_embed | default dict }}
{{- if and (eq (toString $embed.enabled) "true") (eq (toString $embed.render_files) "true") (gt (int $.Values.viewer.replicaCount) 1) (not (or $render.create $render.existingSecret)) }}
{{- fail "render_files=true인 저장소가 있고 viewer.replicaCount>1이면 viewer.renderTokenSecret의 create 또는 existingSecret이 필요합니다" }}
{{- end }}
{{- end }}
{{- end }}
{{- if not (include "synctx.enabledServices" .) }}
{{- fail "켜진 서비스가 없습니다. viewer·cdn·marp·slides·drawio·sync·feedback 중 하나 이상의 enabled를 true로 두세요" }}
{{- end }}
{{- if and (include "synctx.isEnabled" .Values.viewer) (not (include "synctx.isEnabled" .Values.cdn)) .Values.synctxConfig.create }}
{{- $cfg := fromYaml (.Values.synctxConfig.data | default "") }}
{{- $url := dig "viewer" "cdn_base_url" "/cdn" $cfg }}
{{- if and (hasPrefix "/" $url) (not (hasPrefix "//" $url)) }}
{{- fail (printf "cdn.enabled=false이면 .synctx.yaml의 viewer.cdn_base_url을 내부 CDN 절대 주소로 지정해야 합니다(현재 %q)" $url) }}
{{- end }}
{{- end }}
{{- if and .Values.metrics.enabled (or (include "synctx.isEnabled" .Values.sync) .Values.feedback.enabled) (not .Values.sync.persistence.enabled) }}
{{- fail "metrics.enabled=true이고 sync나 feedback이 켜져 있으면 sync.persistence.enabled=true여야 합니다. Sync·Feedback 메트릭 누적값을 sync-state PVC에 두므로 PVC 없이는 Sync·Feedback Alert가 울리지 않습니다. 메트릭이 필요 없으면 metrics.enabled=false로 두세요" }}
{{- end }}
{{- end }}
