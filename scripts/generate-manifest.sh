#!/usr/bin/env bash
set -euo pipefail

# synctx 오프라인 반입 artifact SHA-256 manifest 생성
# 용도: 폐쇄망 반입 시 무결성 검증
# 사용법: bash scripts/generate-manifest.sh [output_file]

OUTPUT="${1:-manifest.sha256}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "# synctx artifact manifest (generated $(date -u +%Y-%m-%dT%H:%M:%SZ))" > "$OUTPUT"
echo "# Verify: sha256sum -c $OUTPUT" >> "$OUTPUT"
echo "" >> "$OUTPUT"

# 1. Docker images (from values-production.yaml)
echo "# --- Docker Images ---" >> "$OUTPUT"
if command -v docker &>/dev/null; then
    images=(
        "registry.internal/synctx/viewer:0.1.0"
        "registry.internal/synctx/cdn:0.1.0"
        "registry.internal/synctx/sync:0.1.0"
        "registry.internal/synctx/marp:0.1.0"
        "registry.internal/synctx/feedback:0.1.0"
        "registry.internal/nginx:1.25.4-alpine"
        "registry.internal/alpine/git:2.43.0"
        "registry.internal/jgraph/drawio:24.2.5"
        "registry.internal/prom/pushgateway:v1.11.1"
    )
    for img in "${images[@]}"; do
        digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$img" 2>/dev/null || echo "NOT_AVAILABLE")
        echo "# image: $img -> $digest" >> "$OUTPUT"
    done
else
    echo "# docker not available - image digests skipped" >> "$OUTPUT"
fi
echo "" >> "$OUTPUT"

# 2. CDN vendor files
echo "# --- CDN Vendor Files ---" >> "$OUTPUT"
if [ -d "$PROJECT_ROOT/cdn-data" ]; then
    find "$PROJECT_ROOT/cdn-data" -type f | sort | while read -r f; do
        rel="${f#$PROJECT_ROOT/}"
        sha=$(sha256sum "$f" | cut -d' ' -f1)
        echo "$sha  $rel" >> "$OUTPUT"
    done
fi
echo "" >> "$OUTPUT"

# 3. Helm chart
echo "# --- Helm Chart ---" >> "$OUTPUT"
if [ -d "$PROJECT_ROOT/helm/synctx" ]; then
    find "$PROJECT_ROOT/helm/synctx" -type f | sort | while read -r f; do
        rel="${f#$PROJECT_ROOT/}"
        sha=$(sha256sum "$f" | cut -d' ' -f1)
        echo "$sha  $rel" >> "$OUTPUT"
    done
fi
echo "" >> "$OUTPUT"

# 4. Dockerfiles
echo "# --- Dockerfiles ---" >> "$OUTPUT"
find "$PROJECT_ROOT/services" -name "Dockerfile" | sort | while read -r f; do
    rel="${f#$PROJECT_ROOT/}"
    sha=$(sha256sum "$f" | cut -d' ' -f1)
    echo "$sha  $rel" >> "$OUTPUT"
done

echo ""
echo "Manifest generated: $OUTPUT"
wc -l < "$OUTPUT" | xargs -I{} echo "{} lines"
