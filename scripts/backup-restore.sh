#!/usr/bin/env bash
set -euo pipefail

# synctx PVC 데이터 백업/복구 스크립트
# 용도: CDN 파일, Sync 상태, Slides 출판물, Feedback 이벤트 백업
# 사용법:
#   backup:  bash scripts/backup-restore.sh backup [backup_dir]
#   restore: bash scripts/backup-restore.sh restore <backup_dir>
#   list:    bash scripts/backup-restore.sh list [backup_root]

NAMESPACE="${SYNCTX_NAMESPACE:-synctx}"
BACKUP_ROOT="${SYNCTX_BACKUP_ROOT:-/data/backups}"

PVCS=(
    "cdn-storage:/data/cdn"
    "sync-state:/data/sync-state"
    "slides-storage:/data/slides"
)

backup() {
    local backup_dir="${1:-$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "$backup_dir"

    echo "=== synctx backup ==="
    echo "Target: $backup_dir"
    echo ""

    for entry in "${PVCS[@]}"; do
        IFS=":" read -r name path <<< "$entry"
        local dest="$backup_dir/$name"
        mkdir -p "$dest"

        echo "Backing up $name ($path) ..."

        if kubectl get pvc "$name" -n "$NAMESPACE" &>/dev/null; then
            # K8s 환경: 임시 pod로 tar 추출
            kubectl run "synctx-backup-$name" \
                -n "$NAMESPACE" \
                --restart=Never \
                --image=busybox \
                --overrides="{
                    \"spec\":{\"containers\":[{
                        \"name\":\"backup\",
                        \"image\":\"busybox\",
                        \"command\":[\"tar\",\"czf\",\"-\",\"-C\",\"$path\",\".\"],
                        \"volumeMounts\":[{\"name\":\"data\",\"mountPath\":\"$path\"}]
                    }],\"volumes\":[{\"name\":\"data\",\"persistentVolumeClaim\":{\"claimName\":\"$name\"}}]}
                }" \
                -- tar czf - -C "$path" . > "$dest/data.tar.gz" 2>/dev/null

            kubectl delete pod "synctx-backup-$name" -n "$NAMESPACE" --wait=false 2>/dev/null || true
        elif [ -d "$path" ]; then
            # 로컬 환경: 직접 tar
            tar czf "$dest/data.tar.gz" -C "$path" . 2>/dev/null || echo "  [SKIP] $path not accessible"
        else
            echo "  [SKIP] $name: PVC and local path not found"
            continue
        fi

        if [ -f "$dest/data.tar.gz" ]; then
            local size
            size=$(du -sh "$dest/data.tar.gz" | cut -f1)
            echo "  Done: $size"
        fi
    done

    # Metadata
    cat > "$backup_dir/metadata.json" <<EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "namespace": "$NAMESPACE",
    "pvcs": [$(printf '"%s",' "${PVCS[@]}" | sed 's/,$//')],
    "hostname": "$(hostname)"
}
EOF

    echo ""
    echo "Backup complete: $backup_dir"
}

restore() {
    local backup_dir="$1"
    if [ -z "$backup_dir" ] || [ ! -d "$backup_dir" ]; then
        echo "ERROR: Backup directory required and must exist"
        exit 1
    fi

    echo "=== synctx restore ==="
    echo "Source: $backup_dir"
    echo ""

    for entry in "${PVCS[@]}"; do
        IFS=":" read -r name path <<< "$entry"
        local src="$backup_dir/$name/data.tar.gz"

        if [ ! -f "$src" ]; then
            echo "[SKIP] $name: no backup found"
            continue
        fi

        echo "Restoring $name → $path ..."

        if kubectl get pvc "$name" -n "$NAMESPACE" &>/dev/null; then
            kubectl run "synctx-restore-$name" \
                -n "$NAMESPACE" \
                --restart=Never \
                --image=busybox \
                --overrides="{
                    \"spec\":{\"containers\":[{
                        \"name\":\"restore\",
                        \"image\":\"busybox\",
                        \"command\":[\"sh\",\"-c\",\"tar xzf - -C $path\"],
                        \"stdin\":true,
                        \"volumeMounts\":[{\"name\":\"data\",\"mountPath\":\"$path\"}]
                    }],\"volumes\":[{\"name\":\"data\",\"persistentVolumeClaim\":{\"claimName\":\"$name\"}}]}
                }" \
                -i < "$src" 2>/dev/null

            kubectl delete pod "synctx-restore-$name" -n "$NAMESPACE" --wait=false 2>/dev/null || true
        elif [ -d "$path" ] || mkdir -p "$path" 2>/dev/null; then
            tar xzf "$src" -C "$path"
        else
            echo "  [ERROR] Cannot restore $name"
            continue
        fi

        echo "  Done"
    done

    echo ""
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
