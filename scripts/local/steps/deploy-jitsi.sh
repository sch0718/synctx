#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

JITSI_HOST="meet.${DOMAIN}"
ADVERTISE_IPS=""
SKIP_IMAGES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) JITSI_HOST="$2"; shift 2 ;;
        --advertise-ips) ADVERTISE_IPS="$2"; shift 2 ;;
        --skip-images) SKIP_IMAGES=true; shift ;;
        -h|--help)
            echo "사용법: deploy-jitsi.sh [--host meet.synctx.local] [--advertise-ips <JVB IP>] [--skip-images]"
            exit 0 ;;
        *) print_error "알 수 없는 옵션: $1"; exit 1 ;;
    esac
done
[[ "$JITSI_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { print_error "잘못된 Jitsi 호스트"; exit 1; }

print_header "선택 Jitsi 배포"
print_warning "이 배포는 Collab Gateway를 끕니다. 이후 deploy-synctx.sh는 Jitsi를 끕니다(추가 Helm 인자로 덮어쓰지 않은 경우)."
print_warning "4GiB minikube에서는 Mattermost와 동시에 기동하지 마세요"
MATTERMOST_REPLICAS="$(kubectl get deployment mattermost -n "${DEPS_NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
if [[ "${MATTERMOST_REPLICAS:-0}" != 0 ]]; then
    print_error "Mattermost를 먼저 중지하고 다시 실행하세요(메모리 상한 4GiB)"
    exit 1
fi
[ -n "$ADVERTISE_IPS" ] || ADVERTISE_IPS="$(minikube ip)"
export JITSI_HOST ADVERTISE_IPS PROJECT_ROOT

umask 077
JITSI_VALUES_FILE="${PROJECT_ROOT}/helm/synctx/values-local.jitsi.secret.yaml"
export JITSI_VALUES_FILE
uv run --offline --frozen --all-packages python - <<'PY'
import os
import secrets
from pathlib import Path

import yaml

root = Path(os.environ['PROJECT_ROOT'])
cache = Path.home() / '.cache' / 'synctx' / 'jitsi'
cache.mkdir(parents=True, exist_ok=True, mode=0o700)
cache.chmod(0o700)
keys = {}
for name in ('app', 'room', 'jicofo', 'jvb'):
    path = cache / name
    if not path.exists():
        with path.open('x') as stream:
            stream.write(secrets.token_hex(32))
    path.chmod(0o600)
    keys[name] = path.read_text().rstrip('\n')
    if len(keys[name].encode()) < 32:
        raise SystemExit('Jitsi 키 파일은 32바이트 이상이어야 합니다')
if keys['app'] == keys['room']:
    raise SystemExit('Jitsi 앱 키와 방 이름 키는 서로 달라야 합니다')
local = yaml.safe_load((root / 'helm/synctx/values-local.yaml').read_text())
config = yaml.safe_load(local['synctxConfig']['data'])
host = os.environ['JITSI_HOST']
config.setdefault('collab', {})['jitsi'] = dict(enabled=True, public_url=f'https://{host}', app_id='synctx', secret_env='JITSI_APP_SECRET', room_secret_env='JITSI_ROOM_SECRET')
values = dict(
    collabGateway=dict(enabled=False),
    jitsi=dict(enabled=True, host=host, publicUrl=f'https://{host}',
               ingress=dict(annotations={'cert-manager.io/cluster-issuer': 'ca-issuer'}, tlsSecretName='jitsi-tls'),
               jwtSecret=dict(create=True, current=keys['app']),
               roomSecret=dict(create=True, current=keys['room']),
               xmppSecret=dict(create=True, jicofoPassword=keys['jicofo'], jvbPassword=keys['jvb']),
               jvb=dict(advertisedIPs=os.environ['ADVERTISE_IPS'])),
    synctxConfig=dict(create=True, data=yaml.safe_dump(config, allow_unicode=True)))
path = Path(os.environ['JITSI_VALUES_FILE'])
path.write_text(yaml.safe_dump(values, allow_unicode=True))
path.chmod(0o600)
PY

if [ "$SKIP_IMAGES" != true ]; then
    JITSI_IMAGE_TAG="$(sed -n 's/^  imageTag: "\([^" ]*\)".*/\1/p' "${PROJECT_ROOT}/helm/synctx/values.yaml")"
    for component in web prosody jicofo jvb; do
        load_image "jitsi/${component}:${JITSI_IMAGE_TAG}"
    done
fi
ensure_namespace "${NAMESPACE}"
VALUES_ARGS=(-f "${VALUES_LOCAL_FILE}")
if [ -f "${VALUES_LOCAL_SECRET_FILE}" ]; then
    VALUES_ARGS+=(-f "${VALUES_LOCAL_SECRET_FILE}")
fi
helm upgrade --install synctx "${PROJECT_ROOT}/helm/synctx" -n "${NAMESPACE}" \
    "${VALUES_ARGS[@]}" -f "${JITSI_VALUES_FILE}" --wait --timeout 300s
print_success "접속 URL: https://${JITSI_HOST}"
print_info "Ingress IP를 ${JITSI_HOST}의 hosts에 등록하고 mkcert CA·ca-issuer TLS 인증서를 신뢰하세요"
print_info "터널: minikube tunnel -p ${MINIKUBE_PROFILE}"
print_warning "로컬 검증 범위는 접속·입장·데이터 채널까지이며 UDP 미디어는 검증하지 않는다"
print_info "검증: scripts/local/verify.sh --jitsi"
