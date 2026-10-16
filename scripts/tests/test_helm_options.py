"""Helm 서비스 옵션 렌더 테스트. helm이 없으면 건너뛴다."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm" / "synctx"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm 미설치")

DEFAULT_RESOURCES = {
    ("ConfigMap", "synctx-slides-config"),
    ("ConfigMap", "synctx-slides-scripts"),
    ("CronJob", "synctx-sync"),
    ("Deployment", "synctx-cdn"),
    ("Deployment", "synctx-drawio"),
    ("Deployment", "synctx-marp"),
    ("Deployment", "synctx-slides"),
    ("Deployment", "synctx-viewer"),
    ("Ingress", "synctx"),
    ("NetworkPolicy", "synctx-sync-network-policy"),
    ("PersistentVolumeClaim", "synctx-cdn-data"),
    ("PersistentVolumeClaim", "synctx-slides-worktree"),
    ("PersistentVolumeClaim", "synctx-sync-state"),
    ("PrometheusRule", "synctx-alerts"),
    ("Role", "synctx-sync-role"),
    ("RoleBinding", "synctx-sync-rolebinding"),
    ("Service", "synctx-cdn"),
    ("Service", "synctx-drawio"),
    ("Service", "synctx-marp"),
    ("Service", "synctx-slides"),
    ("Service", "synctx-viewer"),
    ("ServiceAccount", "synctx-sync"),
    ("ServiceMonitor", "synctx"),
}
# 서비스를 꺼도 PVC는 남긴다. Helm upgrade가 PVC를 지우면 reclaimPolicy: Delete에서 데이터가 사라진다.
PVC_RESOURCES = {
    ("PersistentVolumeClaim", "synctx-cdn-data"),
    ("PersistentVolumeClaim", "synctx-slides-worktree"),
    ("PersistentVolumeClaim", "synctx-sync-state"),
}
SYNC_RESOURCES = {
    ("CronJob", "synctx-sync"),
    ("NetworkPolicy", "synctx-sync-network-policy"),
    ("Role", "synctx-sync-role"),
    ("RoleBinding", "synctx-sync-rolebinding"),
    ("ServiceAccount", "synctx-sync"),
}


def _render(*args):
    return subprocess.run(
        ["helm", "template", "synctx", str(CHART), *args], capture_output=True, text=True
    )


def _docs(*args):
    result = _render(*args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _names(docs):
    return {(doc["kind"], doc["metadata"]["name"]) for doc in docs}


def _viewer_env(docs):
    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "synctx-viewer")
    return {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0].get("env", [])}


def _ingress_backends(docs):
    ingress = [d for d in docs if d["kind"] == "Ingress"]
    if not ingress:
        return None
    return [
        p["backend"]["service"]["name"]
        for rule in ingress[0]["spec"]["rules"]
        for p in rule["http"]["paths"]
    ]


def _values_file(tmp_path, text):
    path = tmp_path / "values.yaml"
    path.write_text(text)
    return str(path)


def test_default_render_keeps_resources():
    docs = _docs()
    assert _names(docs) == DEFAULT_RESOURCES
    assert _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"] == "viewer,cdn,marp,slides,drawio,sync"
    assert _ingress_backends(docs) == [
        "synctx-viewer", "synctx-cdn", "synctx-marp", "synctx-slides", "synctx-drawio",
    ]


@pytest.mark.parametrize("values", ["values-local.yaml", "values-production.yaml"])
def test_existing_values_files_render(values):
    docs = _docs("-f", str(CHART / values))
    assert {("Deployment", f"synctx-{svc}") for svc in ["viewer", "cdn", "marp", "slides", "drawio"]} <= _names(docs)
    assert _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"].startswith("viewer,cdn,marp,slides,drawio,sync")


def test_viewer_marp_slides_only(tmp_path):
    values = _values_file(tmp_path, """
cdn:
  enabled: false
drawio:
  enabled: false
sync:
  enabled: false
synctxConfig:
  create: true
  data: |
    viewer:
      cdn_base_url: https://cdn.internal/libs
""")
    docs = _docs("-f", values)
    names = _names(docs)
    assert not {n for n in names if n[1] in ("synctx-cdn", "synctx-drawio")}
    assert not (names & SYNC_RESOURCES)
    assert PVC_RESOURCES <= names
    assert ("ConfigMap", "synctx-config") in names
    assert _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"] == "viewer,marp,slides"
    assert _ingress_backends(docs) == ["synctx-viewer", "synctx-marp", "synctx-slides"]


def test_drawio_off_removes_route_and_resources():
    docs = _docs("--set", "drawio.enabled=false")
    names = _names(docs)
    assert ("Deployment", "synctx-drawio") not in names
    assert ("Service", "synctx-drawio") not in names
    assert "synctx-drawio" not in _ingress_backends(docs)
    assert "drawio" not in _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"].split(",")


def _cdn_off_values(tmp_path, data):
    # json.dumps 결과는 YAML 큰따옴표 문자열로도 유효하다(\n 이스케이프 유지)
    return _values_file(tmp_path, "cdn:\n  enabled: false\nsynctxConfig:\n  create: true\n  data: " + json.dumps(data) + "\n")


@pytest.mark.parametrize("data", ["", "viewer:\n  max_render_size_bytes: 10\n", "viewer:\n  cdn_base_url: /cdn\n"])
def test_cdn_off_with_relative_url_fails(tmp_path, data):
    result = _render("-f", _cdn_off_values(tmp_path, data))
    assert result.returncode != 0
    assert "viewer.cdn_base_url" in result.stderr


@pytest.mark.parametrize("url", ["//cdn.internal/libs", "https://cdn.internal/libs"])
def test_cdn_off_with_absolute_url_is_ok(tmp_path, url):
    docs = _docs("-f", _cdn_off_values(tmp_path, f"viewer:\n  cdn_base_url: {url}\n"))
    assert ("Deployment", "synctx-cdn") not in _names(docs)


def test_cdn_off_without_chart_config_is_not_checked():
    docs = _docs("--set", "cdn.enabled=false")
    assert ("Deployment", "synctx-cdn") not in _names(docs)


def test_all_off_fails():
    result = _render(
        "--set", "viewer.enabled=false,cdn.enabled=false,marp.enabled=false,slides.enabled=false",
        "--set", "drawio.enabled=false,sync.enabled=false",
    )
    assert result.returncode != 0
    assert "켜진 서비스가 없습니다" in result.stderr


def test_disabled_services_keep_pvcs():
    docs = _docs("--set", "cdn.enabled=false,slides.enabled=false,sync.enabled=false")
    names = _names(docs)
    assert PVC_RESOURCES <= names
    assert ("Deployment", "synctx-cdn") not in names
    assert ("Deployment", "synctx-slides") not in names


def test_sync_only_has_no_ingress():
    docs = _docs(
        "--set", "viewer.enabled=false,cdn.enabled=false,marp.enabled=false,slides.enabled=false,drawio.enabled=false",
    )
    assert _ingress_backends(docs) is None
    assert SYNC_RESOURCES <= _names(docs)


NEW_FLAGS = ["viewer", "cdn", "marp", "slides", "drawio", "sync"]
REMOVE_FLAGS = ",".join(f"{svc}.enabled=null" for svc in NEW_FLAGS)


@pytest.mark.parametrize("values", [[], ["-f", str(CHART / "values-production.yaml")]])
def test_missing_enabled_keys_mean_on(values):
    # --reuse-values 업그레이드는 enabled 키가 없던 이전 릴리스 값을 쓴다. 키가 없으면 켜진 것으로 본다.
    expected = _docs(*values)
    docs = _docs(*values, "--set", REMOVE_FLAGS)
    assert _names(docs) == _names(expected)
    assert _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"] == _viewer_env(expected)["SYNCTX_ENABLED_SERVICES"]
    assert _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"].startswith(",".join(NEW_FLAGS))
    assert _ingress_backends(docs) == _ingress_backends(expected)


def test_missing_feedback_key_stays_off():
    docs = _docs("-f", str(CHART / "values-production.yaml"), "--set", REMOVE_FLAGS + ",feedback.enabled=null")
    assert "feedback" not in _viewer_env(docs)["SYNCTX_ENABLED_SERVICES"].split(",")
    assert not [d for d in docs if d["metadata"]["name"].startswith("synctx-feedback")]


@pytest.mark.parametrize("backend", ["wiki", "git"])
def test_unknown_ingress_backend_fails(tmp_path, backend):
    values = _values_file(tmp_path, """
ingress:
  hosts:
    - host: synctx.internal
      paths:
        - path: /wiki(/(.*))?
          pathType: ImplementationSpecific
          backend: BACKEND
""".replace("BACKEND", backend))
    result = _render("-f", values)
    assert result.returncode != 0
    assert f'"{backend}"' in result.stderr


def test_notes_warn_when_slides_without_marp():
    result = subprocess.run(
        ["helm", "install", "synctx", str(CHART), "--dry-run=client", "--set", "marp.enabled=false"],
        capture_output=True, text=True, env={**os.environ, "KUBECONFIG": "/dev/null"},
    )
    assert result.returncode == 0, result.stderr
    assert "Marp가 꺼져 있습니다" in result.stdout.split("NOTES:", 1)[1]
