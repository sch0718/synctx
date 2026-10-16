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
{{- end }}
