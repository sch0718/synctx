"""선택 Jitsi 스택과 Viewer 키 연결의 Helm 렌더 검증."""

import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm/synctx"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm 미설치")
CONFIG = {"collab": {"jitsi": {"enabled": True, "public_url": "https://meet.synctx.local", "app_id": "synctx"}}}


def settings():
    return {
        "jitsi": {
            "enabled": True, "host": "meet.synctx.local", "publicUrl": "https://meet.synctx.local",
            "jwtSecret": {"create": True, "current": "a" * 32, "previous": "p" * 32},
            "roomSecret": {"create": True, "current": "r" * 32},
            "xmppSecret": {"create": True, "jicofoPassword": "f" * 32, "jvbPassword": "b" * 32},
            "jvb": {"advertisedIPs": "192.0.2.1"},
        },
        "synctxConfig": {"create": True, "data": yaml.safe_dump(CONFIG)},
    }


def render(tmp_path, data=None, *args):
    path = tmp_path / "jitsi.yaml"
    path.write_text(yaml.safe_dump(settings() if data is None else data))
    return subprocess.run(["helm", "template", "synctx", str(CHART), *args, "-f", str(path)], capture_output=True, text=True)


def manifests(result):
    assert result.returncode == 0, result.stderr
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(result.stdout) if d}


def env(items, component):
    deployment = items["Deployment", f"synctx-{component}"]
    return {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_default_and_explicit_off_have_same_render(tmp_path):
    default = manifests(render(tmp_path, {}))
    off = manifests(render(tmp_path, {"jitsi": {"enabled": False}}))
    assert default == off
    assert not any("jitsi" in name for _, name in default)
    assert "JITSI_APP_SECRET" not in env(default, "viewer")


def test_four_workloads_secrets_probes_resources_and_tls(tmp_path):
    items = manifests(render(tmp_path))
    for c in ("web", "prosody", "jicofo", "jvb"):
        spec = items["Deployment", f"synctx-jitsi-{c}"]["spec"]
        assert spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate"
        pod = spec["template"]["spec"]
        container = pod["containers"][0]
        assert container["image"].startswith(f"jitsi/{c}:stable-")
        assert all(key in container for key in ("startupProbe", "livenessProbe", "readinessProbe", "resources"))
        assert all("emptyDir" in v for v in pod["volumes"])
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        assert ("Service", f"synctx-jitsi-{c}") in items
    viewer = env(items, "viewer")
    prosody = env(items, "jitsi-prosody")
    assert viewer["JITSI_APP_SECRET"]["valueFrom"] == prosody["JWT_APP_SECRET"]["valueFrom"]
    assert viewer["JITSI_APP_SECRET_PREVIOUS"]["valueFrom"]["secretKeyRef"]["optional"]
    assert "JITSI_ROOM_SECRET" not in prosody
    assert prosody["XMPP_DOMAIN"]["value"] == "meet.synctx.local"
    assert prosody["AUTH_TYPE"]["value"] == "jwt"
    assert prosody["JWT_ALLOW_EMPTY"]["value"] == "0"
    assert prosody["XMPP_MUC_MODULES"]["value"] == "token_affiliation"
    assert prosody["ENABLE_AUTO_OWNER"]["value"] == "0"
    ingress = items["Ingress", "synctx-jitsi"]["spec"]
    assert ingress["tls"] == [{"hosts": ["meet.synctx.local"], "secretName": "jitsi-tls"}]
    assert "rewrite-target" not in str(items["Ingress", "synctx-jitsi"])
    service = items["Service", "synctx-jitsi-jvb-media"]["spec"]
    assert service["type"] == "NodePort"
    assert service["ports"] == [{"name": "media", "port": 31000, "targetPort": "media", "protocol": "UDP", "nodePort": 31000}]
    assert env(items, "jitsi-jicofo")["ENABLE_AUTH"]["value"] == "0"
    assert env(items, "jitsi-jicofo")["ENABLE_AUTO_OWNER"]["value"] == "0"
    assert env(items, "jitsi-web")["ENABLE_P2P"]["value"] == "false"
    bridge = env(items, "jitsi-jvb")
    assert bridge["JVB_DISABLE_STUN"]["value"] == "1"
    assert "JICOFO_AUTH_PASSWORD" not in bridge
    assert "JICOFO_AUTH_USER" not in bridge
    assert "JWT_APP_ID" not in bridge
    assert "JVB_AUTH_PASSWORD" not in env(items, "jitsi-jicofo")


def test_external_jitsi_only_deploys_viewer_keys_and_alert(tmp_path):
    data = settings()
    data["jitsi"] = {"enabled": False, "jwtSecret": {"existingSecret": "external-app"}, "roomSecret": {"existingSecret": "external-room"}}
    items = manifests(render(tmp_path, data))
    assert not any(name.startswith("synctx-jitsi") for _, name in items)
    assert env(items, "viewer")["JITSI_APP_SECRET"]["valueFrom"]["secretKeyRef"]["name"] == "external-app"
    assert "SynctxJitsiTokenFailures" in str(items["PrometheusRule", "synctx-alerts"])


def test_existing_secrets_and_custom_env_names(tmp_path):
    data = settings()
    for slot in ("jwtSecret", "roomSecret", "xmppSecret"):
        data["jitsi"][slot] = {"create": False, "existingSecret": slot}
    cfg = copy.deepcopy(CONFIG)
    cfg["collab"]["jitsi"].update(secret_env="CUSTOM_APP", room_secret_env="CUSTOM_ROOM")
    data["synctxConfig"]["data"] = yaml.safe_dump(cfg)
    items = manifests(render(tmp_path, data))
    assert not any(kind == "Secret" and "jitsi" in name for kind, name in items)
    assert "CUSTOM_APP_PREVIOUS" in env(items, "viewer")
    assert "CUSTOM_ROOM" in env(items, "viewer")


def test_hostport_and_tcp_disabled(tmp_path):
    data = settings()
    data["jitsi"]["jvb"].update(exposure="hostPort", tcpFallback=False)
    items = manifests(render(tmp_path, data))
    ports = items["Deployment", "synctx-jitsi-jvb"]["spec"]["template"]["spec"]["containers"][0]["ports"]
    assert next(p for p in ports if p["name"] == "media")["hostPort"] == 10000
    assert not any(p["name"] == "media-tcp" for p in ports)
    assert "type" not in items["Service", "synctx-jitsi-jvb-media"]["spec"]
    assert env(items, "jitsi-jvb")["JVB_PORT"]["value"] == "10000"
    assert items["Service", "synctx-jitsi-jvb-media"]["spec"]["ports"][0]["port"] == 10000


@pytest.mark.parametrize("port", [31000, 32000])
def test_nodeport_uses_same_port_everywhere(tmp_path, port):
    data = settings()
    data["jitsi"]["jvb"]["nodePort"] = port
    items = manifests(render(tmp_path, data))
    assert env(items, "jitsi-jvb")["JVB_PORT"]["value"] == str(port)
    container = items["Deployment", "synctx-jitsi-jvb"]["spec"]["template"]["spec"]["containers"][0]
    assert next(p for p in container["ports"] if p["name"] == "media")["containerPort"] == port
    media = items["Service", "synctx-jitsi-jvb-media"]["spec"]["ports"][0]
    assert media["port"] == media["nodePort"] == port


@pytest.mark.parametrize("addresses", [
    "192.0.2.1,198.51.100.2", "0.0.0.0,255.255.255.255", "2001:db8::1",
    "2001:db8:0:1:2:3:4:5", "::1,192.0.2.1", "::", "::ffff:192.0.2.1",
])
def test_advertised_ip_list(tmp_path, addresses):
    data = settings()
    data["jitsi"]["jvb"]["advertisedIPs"] = addresses
    items = manifests(render(tmp_path, data))
    assert env(items, "jitsi-jvb")["JVB_ADVERTISE_IPS"]["value"] == addresses


@pytest.mark.parametrize("addresses", ["192.0.2.1#31000", "meet.synctx.local", "999.1.2.3", "192.0.2.1,", "192.0.2.1, 198.51.100.2", "2001:::1", "1:2:3", "[::1]", "::ffff:999.1.2.3"])
def test_invalid_advertised_ip_list(tmp_path, addresses):
    data = settings()
    data["jitsi"]["jvb"]["advertisedIPs"] = addresses
    result = render(tmp_path, data)
    assert result.returncode != 0 and "advertisedIPs" in result.stderr


@pytest.mark.parametrize("component", ["jicofo", "jvb"])
def test_short_xmpp_password_rejected(tmp_path, component):
    data = settings()
    data["jitsi"]["xmppSecret"][component + "Password"] = "x" * 31
    result = render(tmp_path, data)
    assert result.returncode != 0 and "32" in result.stderr


def test_image_tag_override_applies_to_all_components(tmp_path):
    items = manifests(render(tmp_path, None, "--set", "jitsi.imageTag=stable-9823-1"))
    for component in ("web", "prosody", "jicofo", "jvb"):
        container = items["Deployment", f"synctx-jitsi-{component}"]["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == f"jitsi/{component}:stable-9823-1"


@pytest.mark.parametrize("option", [
    "jitsi.host=", "jitsi.publicUrl=https://other", "jitsi.appId=other", "jitsi.jwtSecret.create=false",
    "jitsi.roomSecret.current=short", "jitsi.jwtSecret.previous=short", "jitsi.jwtSecret.existingSecret=duplicate",
    "jitsi.xmppSecret.create=false", "jitsi.xmppSecret.jvbPassword=", "synctxConfig.create=false",
    "jitsi.jvb.advertisedIPs=", "jitsi.jvb.exposure=LoadBalancer", "jitsi.imageTag=latest",
    "jitsi.jvb.nodePort=10000", "jitsi.ingress.tlsSecretName=", "viewer.enabled=false",
    "jitsi.roomSecret.current=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
])
def test_invalid_settings_rejected(tmp_path, option):
    result = render(tmp_path, None, "--set", option)
    assert result.returncode != 0


def test_enabled_with_local_values(tmp_path):
    items = manifests(render(tmp_path, None, "-f", str(CHART / "values-local.yaml")))
    assert ("Deployment", "synctx-jitsi-web") in items


def test_metrics_can_be_disabled(tmp_path):
    assert not any(kind == "PrometheusRule" for kind, _ in manifests(render(tmp_path, None, "--set", "metrics.enabled=false")))


def test_old_values_without_jitsi_key_render(tmp_path):
    assert render(tmp_path, {}, "--set", "jitsi=null").returncode == 0


@pytest.mark.parametrize("config_update", [
    {"xmpp_domain": "different.example"},
    {"secret_env": "APP", "room_secret_env": "APP_PREVIOUS"},
    {"app_id": ""},
    {"enabled": False},
])
def test_config_disagreement_rejected(tmp_path, config_update):
    data = settings()
    cfg = copy.deepcopy(CONFIG)
    cfg["collab"]["jitsi"].update(config_update)
    data["synctxConfig"]["data"] = yaml.safe_dump(cfg)
    assert render(tmp_path, data).returncode != 0


def test_image_import_and_compose_use_shared_tag():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    tag = values["jitsi"]["imageTag"]
    assert tag == "stable-9823"
    assert all("tag" not in image for image in values["jitsi"]["images"].values())
    production = yaml.safe_load((CHART / "values-production.yaml").read_text())
    manifest = (ROOT / "scripts/generate-manifest.sh").read_text()
    for c in ("web", "prosody", "jicofo", "jvb"):
        assert production["jitsi"]["images"][c]["repository"] == f"registry.local:5000/jitsi/{c}"
        assert f'registry.local:5000/jitsi/{c}:${{JITSI_IMAGE_TAG}}' in manifest
    assert 'imageTag: "' in (ROOT / "scripts/docker/_common.sh").read_text()
    assert 'imageTag: "' in manifest


def test_manifest_reads_shared_tag_with_inline_comment(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "generate-manifest.sh"
    shutil.copy(ROOT / "scripts/generate-manifest.sh", script)
    chart = tmp_path / "helm/synctx"
    chart.mkdir(parents=True)
    shutil.copy(CHART / "values.yaml", chart / "values.yaml")
    (tmp_path / "services").mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf "fake-digest\\n"\n')
    docker.chmod(0o755)
    output = tmp_path / "manifest.sha256"
    result = subprocess.run(["bash", str(script), str(output)], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    tag = yaml.safe_load((CHART / "values.yaml").read_text())["jitsi"]["imageTag"]
    for component in ("web", "prosody", "jicofo", "jvb"):
        assert f"# image: registry.local:5000/jitsi/{component}:{tag} -> fake-digest\n" in output.read_text()
