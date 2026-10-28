"""Collab Gateway Helm 렌더·검증 테스트. helm이 없으면 건너뛴다."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "helm" / "synctx"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm이 없으면 차트 렌더 검사를 건너뜁니다")

GATEWAY_YAML = """collab:
  gateway:
    enabled: true
    mattermost: {url: "https://chat"}
    channels: {dx: {team: t, channel: c}}
"""


def values(tmp_path, **overrides):
    data = {
        "collabGateway": {"enabled": True, "secrets": {"create": True, "botToken": "bot", "viewerServiceToken": "svc"}},
        "synctxConfig": {"create": True, "data": GATEWAY_YAML},
    }
    def merge(base, extra):
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value

    merge(data, overrides)
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def render(tmp_path, *extra, **overrides):
    result = subprocess.run(["helm", "template", "t", str(CHART), "-f", str(values(tmp_path, **overrides)), *extra],
                            capture_output=True, text=True)
    return result


def docs(result):
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def by_kind(items, kind, name_part=""):
    return [d for d in items if d["kind"] == kind and name_part in d["metadata"]["name"]]


def env_of(deployment):
    return {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])}


def test_disabled_by_default_renders_nothing_for_gateway():
    result = subprocess.run(["helm", "template", "t", str(CHART)], capture_output=True, text=True)
    items = docs(result)
    assert not [d for d in items if "collab-gateway" in d["metadata"]["name"]]
    assert "VIEWER_SERVICE_TOKEN" not in env_of(by_kind(items, "Deployment", "viewer")[0])


def test_missing_values_key_for_old_releases_still_renders(tmp_path):
    result = subprocess.run(["helm", "template", "t", str(CHART), "--set", "collabGateway=null"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_enabled_renders_deployment_service_pvc_secret(tmp_path):
    items = docs(render(tmp_path))
    deploy = by_kind(items, "Deployment", "collab-gateway")[0]
    assert deploy["spec"]["replicas"] == 1 and deploy["spec"]["strategy"]["type"] == "Recreate"
    env = env_of(deploy)
    assert env["MATTERMOST_BOT_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "t-synctx-collab-gateway", "key": "bot-token"}
    assert env["VIEWER_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "viewer-service-token"
    assert env["MATTERMOST_COMMAND_TOKEN"]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert env["SYNCTX_COLLAB_STATE_PATH"]["value"] == "/data/collab-gateway"
    spec = deploy["spec"]["template"]["spec"]
    assert any(v.get("persistentVolumeClaim", {}).get("claimName") == "t-synctx-collab-gateway-state" for v in spec["volumes"])
    assert by_kind(items, "PersistentVolumeClaim", "collab-gateway-state")
    service = by_kind(items, "Service", "collab-gateway")[0]
    assert service["metadata"]["labels"]["synctx/metrics"] == "true"
    secret = by_kind(items, "Secret", "collab-gateway")[0]
    assert set(secret["stringData"]) == {"bot-token", "viewer-service-token"}


def test_viewer_gets_service_token_from_same_secret(tmp_path):
    items = docs(render(tmp_path))
    ref = env_of(by_kind(items, "Deployment", "viewer")[0])["VIEWER_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "t-synctx-collab-gateway", "key": "viewer-service-token"}


def test_enabled_services_env_lists_gateway(tmp_path):
    items = docs(render(tmp_path))
    assert "collab-gateway" in env_of(by_kind(items, "Deployment", "viewer")[0])["SYNCTX_ENABLED_SERVICES"]["value"]


def test_command_token_included_when_given(tmp_path):
    items = docs(render(tmp_path, collabGateway={"secrets": {"commandToken": "cmd"}}))
    assert "command-token" in by_kind(items, "Secret", "collab-gateway")[0]["stringData"]


def test_existing_secret_is_referenced_and_not_created(tmp_path):
    items = docs(render(tmp_path, collabGateway={"secrets": {"create": False, "existingSecret": "mine"}}))
    assert not by_kind(items, "Secret", "collab-gateway")
    env = env_of(by_kind(items, "Deployment", "collab-gateway")[0])
    assert env["MATTERMOST_BOT_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "mine"
    assert env_of(by_kind(items, "Deployment", "viewer")[0])["VIEWER_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "mine"


def test_network_policy_limits_egress(tmp_path):
    items = docs(render(tmp_path, collabGateway={"networkPolicy": {"mattermost": {"namespace": "synctx-deps", "port": 8065}}}))
    policy = by_kind(items, "NetworkPolicy", "collab-gateway")[0]
    assert policy["spec"]["policyTypes"] == ["Egress"]
    rules = policy["spec"]["egress"]
    assert any(p["port"] == 53 for r in rules for p in r["ports"])
    assert any(r.get("to", [{}])[0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "viewer" for r in rules)
    mm = [r for r in rules if r["ports"][0]["port"] == 8065][0]
    assert mm["to"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] == "synctx-deps"


def test_network_policy_without_namespace_only_limits_port(tmp_path):
    items = docs(render(tmp_path))
    rules = by_kind(items, "NetworkPolicy", "collab-gateway")[0]["spec"]["egress"]
    mm = [r for r in rules if r["ports"][0]["port"] == 443][0]
    assert "to" not in mm


def test_network_policy_can_be_disabled(tmp_path):
    items = docs(render(tmp_path, collabGateway={"networkPolicy": {"enabled": False}}))
    assert not by_kind(items, "NetworkPolicy", "collab-gateway")


def test_alerts_only_when_enabled_and_metrics_on(tmp_path):
    on = docs(render(tmp_path, "--set", "metrics.enabled=true"))
    alerts = [r["alert"] for g in by_kind(on, "PrometheusRule")[0]["spec"]["groups"] for r in g["rules"]]
    for name in ("SynctxCollabNotificationFailures", "SynctxCollabNotificationGaveUp", "SynctxCollabViewerFetchFailing",
                 "SynctxCollabStalled", "SynctxCollabUnmappedChannel", "SynctxCollabCommandUnauthorized"):
        assert name in alerts
    off = subprocess.run(["helm", "template", "t", str(CHART), "--set", "metrics.enabled=true"], capture_output=True, text=True)
    assert "SynctxCollab" not in off.stdout


@pytest.mark.parametrize("overrides,message", [
    ({"collabGateway": {"replicaCount": 2}}, "복제본을 1개"),
    ({"collabGateway": {"persistence": {"enabled": False}}}, "persistence"),
    ({"collabGateway": {"secrets": {"create": False}}}, "secrets.create"),
    ({"collabGateway": {"secrets": {"existingSecret": "x"}}}, "함께 지정할 수 없습니다"),
    ({"collabGateway": {"secrets": {"botToken": ""}}}, "botToken"),
    ({"viewer": {"enabled": False}}, "viewer가 켜져"),
    ({"synctxConfig": {"create": False}}, "synctxConfig.create=true"),
    ({"synctxConfig": {"create": True, "data": GATEWAY_YAML.replace("enabled: true", "enabled: false")}}, "collab.gateway.enabled"),
    ({"synctxConfig": {"create": True, "data": GATEWAY_YAML.replace("enabled: true", "enabled: true\n    viewer_service_token_env: OTHER")}}, "기본값"),
])
def test_validation_failures(tmp_path, overrides, message):
    result = render(tmp_path, **overrides)
    assert result.returncode != 0 and message in result.stderr
