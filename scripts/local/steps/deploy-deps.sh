#!/bin/bash
# deploy-deps.sh - 외부 의존성 배포 (XWiki, OpenProject, Mailpit)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

SKIP_XWIKI=false
SKIP_OP=false
SKIP_MAILHOG=false
SKIP_IMAGES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-xwiki)    SKIP_XWIKI=true; shift ;;
        --skip-op)       SKIP_OP=true; shift ;;
        --skip-mailhog)  SKIP_MAILHOG=true; shift ;;
        --skip-images)   SKIP_IMAGES=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--skip-xwiki] [--skip-op] [--skip-mailhog] [--skip-images]"
            exit 0
            ;;
        *) print_error "알 수 없는 옵션: $1"; exit 1 ;;
    esac
done

ensure_namespace "${DEPS_NAMESPACE}"

# ============================================================
# XWiki (PostgreSQL + XWiki)
# ============================================================
if [ "$SKIP_XWIKI" = false ]; then
    print_header "XWiki 배포"

    if [ "$SKIP_IMAGES" = false ]; then
        load_image "postgres:16-alpine"
        load_image "xwiki:16-postgres-tomcat"
    fi

    kubectl apply -n "${DEPS_NAMESPACE}" -f - <<'XWIKI_EOF'
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: xwiki-pg-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xwiki-postgres
  labels:
    app: xwiki-postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: xwiki-postgres
  template:
    metadata:
      labels:
        app: xwiki-postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB
              value: xwiki
            - name: POSTGRES_USER
              value: xwiki
            - name: POSTGRES_PASSWORD
              value: xwiki-local-password
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: pg-data
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: pg-data
          persistentVolumeClaim:
            claimName: xwiki-pg-data
---
apiVersion: v1
kind: Service
metadata:
  name: xwiki-postgres
spec:
  selector:
    app: xwiki-postgres
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: xwiki-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xwiki
  labels:
    app: xwiki
spec:
  replicas: 1
  selector:
    matchLabels:
      app: xwiki
  template:
    metadata:
      labels:
        app: xwiki
    spec:
      containers:
        - name: xwiki
          image: xwiki:16-postgres-tomcat
          env:
            - name: DB_HOST
              value: xwiki-postgres
            - name: DB_USER
              value: xwiki
            - name: DB_PASSWORD
              value: xwiki-local-password
            - name: DB_DATABASE
              value: xwiki
            - name: JAVA_OPTS
              value: "-Xmx1536m -Xms512m"
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: xwiki-data
              mountPath: /usr/local/xwiki
          resources:
            requests:
              cpu: 200m
              memory: 1536Mi
            limits:
              cpu: 1000m
              memory: 3Gi
      volumes:
        - name: xwiki-data
          persistentVolumeClaim:
            claimName: xwiki-data
---
apiVersion: v1
kind: Service
metadata:
  name: xwiki
spec:
  selector:
    app: xwiki
  ports:
    - port: 8080
      targetPort: 8080
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: xwiki
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    cert-manager.io/cluster-issuer: ca-issuer
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - xwiki.synctx.local
      secretName: xwiki-tls
  rules:
    - host: xwiki.synctx.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: xwiki
                port:
                  number: 8080
XWIKI_EOF

    print_success "XWiki 배포 완료"
    print_info "접속: https://xwiki.synctx.local (초기 설정 필요)"
    setup_hosts "xwiki.synctx.local"
fi

# ============================================================
# OpenProject
# ============================================================
if [ "$SKIP_OP" = false ]; then
    print_header "OpenProject 배포"

    if [ "$SKIP_IMAGES" = false ]; then
        load_image "openproject/openproject:15"
    fi

    kubectl apply -n "${DEPS_NAMESPACE}" -f - <<'OP_EOF'
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: openproject-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: openproject
  labels:
    app: openproject
spec:
  replicas: 1
  selector:
    matchLabels:
      app: openproject
  template:
    metadata:
      labels:
        app: openproject
    spec:
      containers:
        - name: openproject
          image: openproject/openproject:15
          env:
            - name: OPENPROJECT_SECRET_KEY_BASE
              value: "local-dev-secret-key-base-synctx-2024"
            - name: OPENPROJECT_HOST__NAME
              value: "openproject.synctx.local"
            - name: OPENPROJECT_HTTPS
              value: "false"
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: op-data
              mountPath: /var/openproject/assets
          resources:
            requests:
              cpu: 200m
              memory: 1Gi
            limits:
              cpu: 1000m
              memory: 2Gi
      volumes:
        - name: op-data
          persistentVolumeClaim:
            claimName: openproject-data
---
apiVersion: v1
kind: Service
metadata:
  name: openproject
spec:
  selector:
    app: openproject
  ports:
    - port: 8080
      targetPort: 8080
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: openproject
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    cert-manager.io/cluster-issuer: ca-issuer
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - openproject.synctx.local
      secretName: openproject-tls
  rules:
    - host: openproject.synctx.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: openproject
                port:
                  number: 8080
OP_EOF

    print_success "OpenProject 배포 완료"
    print_info "접속: https://openproject.synctx.local (admin/admin)"
    setup_hosts "openproject.synctx.local"
fi

# ============================================================
# Mailpit (SMTP 테스트, Mailhog 후속 - ARM64 지원)
# ============================================================
if [ "$SKIP_MAILHOG" = false ]; then
    print_header "Mailpit 배포"

    if [ "$SKIP_IMAGES" = false ]; then
        load_image "axllent/mailpit:latest"
    fi

    kubectl apply -n "${DEPS_NAMESPACE}" -f - <<'MAIL_EOF'
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mailpit
  labels:
    app: mailpit
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mailpit
  template:
    metadata:
      labels:
        app: mailpit
    spec:
      containers:
        - name: mailpit
          image: axllent/mailpit:latest
          ports:
            - name: smtp
              containerPort: 1025
            - name: http
              containerPort: 8025
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
---
apiVersion: v1
kind: Service
metadata:
  name: mailpit
spec:
  selector:
    app: mailpit
  ports:
    - name: smtp
      port: 1025
      targetPort: 1025
    - name: http
      port: 8025
      targetPort: 8025
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mailpit
  annotations:
    cert-manager.io/cluster-issuer: ca-issuer
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - mail.synctx.local
      secretName: mailpit-tls
  rules:
    - host: mail.synctx.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: mailpit
                port:
                  number: 8025
MAIL_EOF

    print_success "Mailpit 배포 완료"
    print_info "웹 UI: https://mail.synctx.local"
    print_info "SMTP: mailpit.${DEPS_NAMESPACE}.svc.cluster.local:1025"
    setup_hosts "mail.synctx.local"
fi

# 상태 확인
print_header "의존성 배포 상태"
kubectl get pods -n "${DEPS_NAMESPACE}" 2>/dev/null || true
echo ""
kubectl get ingress -n "${DEPS_NAMESPACE}" 2>/dev/null || true
