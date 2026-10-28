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
Collab Gateway를 켰을 때 "true". collabGateway 키가 없던 이전 릴리스 값으로 --reuse-values 업그레이드해도 렌더가 깨지지 않게 dig로 읽는다.
*/}}
{{- define "synctx.collabEnabled" -}}
{{- if eq (toString (dig "enabled" false (.Values.collabGateway | default dict))) "true" }}true{{- end }}
{{- end }}

{{/*
Collab Gateway Secret 이름. 서비스 토큰은 Viewer도 같은 Secret에서 읽는다.
*/}}
{{- define "synctx.collabSecretName" -}}
{{- $cg := .Values.collabGateway | default dict }}
{{- (dig "secrets" "existingSecret" "" $cg) | default (printf "%s-collab-gateway" (include "synctx.fullname" .)) }}
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
{{- if include "synctx.collabEnabled" . }}
{{- $enabled = append $enabled "collab-gateway" }}
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
{{- if include "synctx.collabEnabled" . }}
{{- $cg := .Values.collabGateway }}
{{- if hasKey $cg "replicaCount" }}
{{- fail "collabGateway는 복제본을 1개로 고정합니다. replicaCount를 지정하지 마세요" }}
{{- end }}
{{- if not (include "synctx.isEnabled" .Values.viewer) }}
{{- fail "collabGateway.enabled=true이면 회의 목록을 읽을 viewer가 켜져 있어야 합니다" }}
{{- end }}
{{- if not $cg.persistence.enabled }}
{{- fail "collabGateway.persistence.enabled=false이면 재시작 때 알림 중복 방지 상태를 잃습니다. 켜 두세요" }}
{{- end }}
{{- if not (or $cg.secrets.create $cg.secrets.existingSecret) }}
{{- fail "collabGateway.enabled=true이면 collabGateway.secrets.create=true 또는 existingSecret이 필요합니다" }}
{{- end }}
{{- if and $cg.secrets.create $cg.secrets.existingSecret }}
{{- fail "collabGateway.secrets.create와 existingSecret은 함께 지정할 수 없습니다" }}
{{- end }}
{{- if not .Values.synctxConfig.create }}
{{- fail "collabGateway.enabled=true이면 synctxConfig.create=true로 .synctx.yaml의 collab.gateway를 함께 배포해야 합니다" }}
{{- end }}
{{- $gw := dig "collab" "gateway" dict (fromYaml (.Values.synctxConfig.data | default "")) }}
{{- if ne (toString (dig "enabled" false $gw)) "true" }}
{{- fail "collabGateway.enabled=true이면 .synctx.yaml의 collab.gateway.enabled도 true여야 합니다" }}
{{- end }}
{{- $mm := dig "mattermost" dict $gw }}
{{- if or (ne (dig "bot_token_env" "MATTERMOST_BOT_TOKEN" $mm) "MATTERMOST_BOT_TOKEN") (ne (dig "command_token_env" "MATTERMOST_COMMAND_TOKEN" $mm) "MATTERMOST_COMMAND_TOKEN") (ne (dig "viewer_service_token_env" "VIEWER_SERVICE_TOKEN" $gw) "VIEWER_SERVICE_TOKEN") }}
{{- fail "차트가 만드는 Secret을 쓰려면 collab.gateway의 토큰 환경변수 이름(bot_token_env·command_token_env·viewer_service_token_env)을 기본값으로 두세요" }}
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
