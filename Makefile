.PHONY: all build test lint helm-lint manifest clean smoke-test

REGISTRY ?= registry.internal/synctx
VERSION  ?= 0.1.0
GIT_SHA  := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
TAG      := $(VERSION)-$(GIT_SHA)

SERVICES := viewer cdn marp sync feedback

# ──────────────────────────── Build ────────────────────────────

all: build

build: $(addprefix build-,$(SERVICES))

build-%:
	docker build \
		-t $(REGISTRY)/$*:$(TAG) \
		-f services/$*/Dockerfile \
		.

# ──────────────────────────── Offline ────────────────────────────

save-images: build
	mkdir -p dist/images
	@for svc in $(SERVICES); do \
		echo "Saving $(REGISTRY)/$$svc:$(TAG) ..."; \
		docker save $(REGISTRY)/$$svc:$(TAG) | gzip > dist/images/$$svc-$(TAG).tar.gz; \
	done
	@echo "Saving third-party images ..."
	docker pull nginx:1.25.4-alpine 2>/dev/null || true
	docker save nginx:1.25.4-alpine | gzip > dist/images/nginx-1.25.4-alpine.tar.gz 2>/dev/null || true
	docker pull alpine/git:2.43.0 2>/dev/null || true
	docker save alpine/git:2.43.0 | gzip > dist/images/alpine-git-2.43.0.tar.gz 2>/dev/null || true
	docker pull jgraph/drawio:24.2.5 2>/dev/null || true
	docker save jgraph/drawio:24.2.5 | gzip > dist/images/drawio-24.2.5.tar.gz 2>/dev/null || true
	@echo "All images saved to dist/images/"

load-images:
	@for f in dist/images/*.tar.gz; do \
		echo "Loading $$f ..."; \
		docker load < "$$f"; \
	done

# ──────────────────────────── Test ────────────────────────────

test:
	python -m pytest shared/synctx-common/tests/ \
		services/feedback/tests/ \
		services/marp/tests/ \
		services/sync/tests/ \
		scripts/tests/ \
		-q

# ──────────────────────────── Helm ────────────────────────────

helm-lint:
	helm lint helm/synctx
	helm lint helm/synctx -f helm/synctx/values-production.yaml

helm-template:
	helm template synctx helm/synctx -f helm/synctx/values-production.yaml

# ──────────────────────────── Manifest ────────────────────────────

manifest:
	bash scripts/generate-manifest.sh manifest.sha256

# ──────────────────────────── Smoke Test ────────────────────────────

smoke-test:
	bash scripts/smoke-test.sh

# ──────────────────────────── Clean ────────────────────────────

clean:
	rm -rf dist/ manifest.sha256
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
