#!/usr/bin/env bash
set -euo pipefail

# synctx PVC 데이터 백업/복구 스크립트
# 용도: CDN 파일, Sync 상태(Feedback 이벤트 포함), Slides 출판물, Collab Gateway 상태 백업
# 사용법:
#   backup:  bash scripts/backup-restore.sh backup [backup_dir]
#   restore: bash scripts/backup-restore.sh restore <backup_dir>
#   list:    bash scripts/backup-restore.sh list [backup_root]
# 환경 변수:
#   SYNCTX_NAMESPACE     K8s 네임스페이스 (기본 synctx)
#   SYNCTX_RELEASE       Helm 차트의 fullname, PVC 이름 앞부분 (기본 synctx)
#   SYNCTX_BACKUP_ROOT   백업 기본 경로 (기본 /data/backups)
#   SYNCTX_HELPER_IMAGE  임시 Pod 이미지, 폐쇄망이면 내부 레지스트리 주소 (기본 busybox)

NAMESPACE="${SYNCTX_NAMESPACE:-synctx}"
RELEASE="${SYNCTX_RELEASE:-synctx}"
BACKUP_ROOT="${SYNCTX_BACKUP_ROOT:-/data/backups}"
HELPER_IMAGE="${SYNCTX_HELPER_IMAGE:-busybox}"

# "<Helm PVC 이름 뒷부분>:<컨테이너 안 경로>". PVC 이름은 helm/synctx/templates/*-pvc.yaml과 같다.
PVCS=(
    "cdn-data:/data/cdn"
    "sync-state:/data/sync-state"
    "slides-worktree:/data/slides/worktree"
    "collab-gateway-state:/data/collab-gateway"
)

HELPER_POD=""

stop_helper() {
    if [ -n "$HELPER_POD" ]; then
        kubectl delete pod "$HELPER_POD" -n "$NAMESPACE" --ignore-not-found --wait=false >/dev/null 2>&1 || true
        HELPER_POD=""
    fi
}
trap stop_helper EXIT

# PVC를 붙인 임시 Pod를 띄우고 Ready까지 기다린다. tar는 kubectl exec로 흘려 보낸다.
# (kubectl run의 출력을 파일로 받으면 "pod/... created" 글자만 담긴다)
start_helper() {
    local pod="$1" claim="$2" path="$3"
    kubectl delete pod "$pod" -n "$NAMESPACE" --ignore-not-found --wait=true >/dev/null
    HELPER_POD="$pod"
    kubectl run "$pod" \
        -n "$NAMESPACE" \
        --restart=Never \
        --image="$HELPER_IMAGE" \
        --overrides="{
            \"spec\":{\"containers\":[{
                \"name\":\"helper\",
                \"image\":\"$HELPER_IMAGE\",
                \"command\":[\"sleep\",\"3600\"],
                \"volumeMounts\":[{\"name\":\"data\",\"mountPath\":\"$path\"}]
            }],\"volumes\":[{\"name\":\"data\",\"persistentVolumeClaim\":{\"claimName\":\"$claim\"}}]}
        }" >/dev/null
    kubectl wait pod "$pod" -n "$NAMESPACE" --for=condition=Ready --timeout=120s >/dev/null
}

backup() {
    local backup_dir="${1:-$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)}"
    local failed=0 done=0
    mkdir -p "$backup_dir"

    echo "=== synctx backup ==="
    echo "Target: $backup_dir"
    echo ""

    for entry in "${PVCS[@]}"; do
        IFS=":" read -r name path <<< "$entry"
        local claim="$RELEASE-$name"
        local dest="$backup_dir/$name"
        local ok=1

        echo "Backing up $claim ($path) ..."

        if kubectl get pvc "$claim" -n "$NAMESPACE" &>/dev/null; then
            mkdir -p "$dest"
            if ! { start_helper "$RELEASE-backup-$name" "$claim" "$path" \
                   && kubectl exec "$HELPER_POD" -n "$NAMESPACE" -- tar czf - -C "$path" . > "$dest/data.tar.gz"; }; then
                ok=0
            fi
            stop_helper
        elif [ -d "$path" ]; then
            mkdir -p "$dest"
            tar czf "$dest/data.tar.gz" -C "$path" . || ok=0
        else
            echo "  [SKIP] $claim: PVC and local path not found"
            continue
        fi

        # 받은 파일이 읽히는 tar.gz인지 확인한다. 아니면 완료로 세지 않는다.
        if [ "$ok" = 1 ] && tar tzf "$dest/data.tar.gz" >/dev/null 2>&1; then
            echo "  Done: $(du -sh "$dest/data.tar.gz" | cut -f1)"
            done=$((done + 1))
        else
            echo "  [FAIL] $claim"
            failed=$((failed + 1))
        fi
    done

    cat > "$backup_dir/metadata.json" <<EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "namespace": "$NAMESPACE",
    "release": "$RELEASE",
    "pvcs": [$(printf '"%s",' "${PVCS[@]}" | sed 's/,$//')],
    "failed": $failed,
    "hostname": "$(hostname)"
}
EOF

    echo ""
    if [ "$failed" -gt 0 ]; then
        echo "Backup finished with $failed failure(s): $backup_dir"
        exit 1
    fi
    if [ "$done" -eq 0 ]; then
        echo "Nothing was backed up (PVC도 로컬 경로도 찾지 못함): $backup_dir"
        exit 1
    fi
    echo "Backup complete: $backup_dir"
}

restore() {
    local backup_dir="$1"
    local failed=0 done=0
    if [ -z "$backup_dir" ] || [ ! -d "$backup_dir" ]; then
        echo "ERROR: Backup directory required and must exist"
        exit 1
    fi
    # kubectl이 있는데 클러스터에 닿지 못하면 운영자 호스트 디스크에 풀지 않고 멈춘다
    if command -v kubectl >/dev/null && ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        echo "ERROR: kubectl이 namespace $NAMESPACE 에 닿지 못합니다. context를 확인해 주세요"
        exit 1
    fi

    echo "=== synctx restore ==="
    echo "Source: $backup_dir"
    echo ""

    for entry in "${PVCS[@]}"; do
        IFS=":" read -r name path <<< "$entry"
        local claim="$RELEASE-$name"
        local src="$backup_dir/$name/data.tar.gz"
        local ok=1

        if [ ! -f "$src" ]; then
            echo "[SKIP] $claim: no backup found"
            continue
        fi

        echo "Restoring $claim → $path ..."

        if kubectl get pvc "$claim" -n "$NAMESPACE" &>/dev/null; then
            if ! { start_helper "$RELEASE-restore-$name" "$claim" "$path" \
                   && kubectl exec -i "$HELPER_POD" -n "$NAMESPACE" -- tar xzf - -C "$path" < "$src"; }; then
                ok=0
            fi
            stop_helper
        elif mkdir -p "$path" 2>/dev/null; then
            tar xzf "$src" -C "$path" || ok=0
        else
            ok=0
        fi

        if [ "$ok" = 1 ]; then
            echo "  Done"
            done=$((done + 1))
        else
            echo "  [FAIL] $claim"
            failed=$((failed + 1))
        fi
    done

    echo ""
    if [ "$failed" -gt 0 ]; then
        echo "Restore finished with $failed failure(s) from: $backup_dir"
        exit 1
    fi
    if [ "$done" -eq 0 ]; then
        echo "Nothing was restored from: $backup_dir"
        exit 1
    fi
    echo "Restore complete from: $backup_dir"
}

list_backups() {
    local root="${1:-$BACKUP_ROOT}"
    if [ ! -d "$root" ]; then
        echo "No backups found at $root"
        return
    fi
    echo "=== Available backups ==="
    for d in "$root"/*/; do
        if [ -f "$d/metadata.json" ]; then
            local ts
            ts=$(python3 -c "import json; print(json.load(open('${d}metadata.json'))['timestamp'])" 2>/dev/null || echo "unknown")
            local size
            size=$(du -sh "$d" | cut -f1)
            echo "  $(basename "$d")  ($ts, $size)"
        fi
    done
}

case "${1:-}" in
    backup)  backup "${2:-}" ;;
    restore) restore "${2:-}" ;;
    list)    list_backups "${2:-}" ;;
    *)
        echo "Usage: $0 {backup|restore|list} [path]"
        exit 1
        ;;
esac
