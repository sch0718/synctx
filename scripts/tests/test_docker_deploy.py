"""scripts/docker/deploy.sh --check 테스트(검사·게이트웨이 설정 생성)."""

import os
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "docker" / "deploy.sh"
COMPOSE = ROOT / "deploy" / "docker" / "compose.yaml"


def _write_env(tmp_path, overrides):
    config = tmp_path / "synctx.yaml"
    config.write_text("authz:\n  enabled: false\n")
    values = {
        "SYNCTX_IMAGE_TAG": "0.1.0-abc1234",
        "SYNCTX_JITSI_IMAGE_TAG": yaml.safe_load((ROOT / "helm/synctx/values.yaml").read_text())["jitsi"]["imageTag"],
        "SYNCTX_SERVICES": "viewer,cdn,marp,slides",
        "SYNCTX_CONFIG_FILE": str(config),
        "SYNCTX_SLIDES_REPO_URL": "https://git.internal/slides.git",
    }
    values.update(overrides)
    env_file = tmp_path / "synctx.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
    return env_file


def _clean_env(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNCTX_")}
    env["SYNCTX_GENERATED_DIR"] = str(tmp_path / "generated")
    return env


def _check(tmp_path, **overrides):
    env_file = _write_env(tmp_path, overrides)
    result = subprocess.run(
        ["bash", str(DEPLOY), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env"), "--check"],
        capture_output=True, text=True, env=_clean_env(tmp_path),
    )
    conf = tmp_path / "generated" / "gateway.check.conf"
    return result, (conf.read_text() if conf.exists() else "")


def test_check_does_not_overwrite_live_gateway_conf(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    live = generated / "gateway.conf"
    live.write_text("# live config\n")
    result, conf = _check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert live.read_text() == "# live config\n"
    assert "location ~* ^/view(" in conf
    assert "gateway.check.conf" in result.stdout


def test_default_options_render_routes(tmp_path):
    result, conf = _check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    for svc, upstream in [("view", "viewer:8000"), ("cdn", "cdn:8000"), ("marp", "marp:8000"), ("slides", "slides:80")]:
        assert f"location ~* ^/{svc}(/.*)?$" in conf
        assert f"set $synctx_upstream http://{upstream};" in conf
    assert "/drawio" not in conf
    assert 'rewrite "(?i)^/view(?:/(.*))?$" /$1 break;' in conf
    assert "resolver 127.0.0.11 valid=10s ipv6=off;" in conf
    assert "client_max_body_size 50m;" in conf
    assert "listen 80;" in conf
    assert "ssl" not in conf


def test_auth_headers_are_cleared_by_default(tmp_path):
    _, conf = _check(tmp_path)
    for header in ["X-Forwarded-User", "X-Forwarded-Email", "X-Forwarded-Groups"]:
        assert f'proxy_set_header {header} "";' in conf


def test_trusted_auth_proxy_keeps_headers_and_restricts_source(tmp_path):
    result, conf = _check(
        tmp_path,
        SYNCTX_TRUSTED_AUTH_PROXY="true",
        SYNCTX_TRUSTED_PROXY_CIDRS="10.1.2.3,10.9.0.0/16",
        SYNCTX_BIND_ADDRESS="10.0.0.5",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'X-Forwarded-Groups ""' not in conf
    assert "allow 10.1.2.3;" in conf
    assert "allow 10.9.0.0/16;" in conf
    assert "deny all;" in conf


@pytest.mark.parametrize("bind", [None, "0.0.0.0", "::"])
def test_trusted_auth_proxy_rejects_open_bind(tmp_path, bind):
    result, _ = _check(
        tmp_path,
        SYNCTX_TRUSTED_AUTH_PROXY="true",
        SYNCTX_TRUSTED_PROXY_CIDRS="10.1.2.3",
        SYNCTX_BIND_ADDRESS=bind,
    )
    assert result.returncode != 0
    assert "SYNCTX_BIND_ADDRESS" in result.stdout + result.stderr


def test_default_mode_has_no_allow_list(tmp_path):
    _, conf = _check(tmp_path)
    assert "deny all;" not in conf


def test_marp_route_waits_longer_than_conversion(tmp_path):
    _, conf = _check(tmp_path)
    marp_block = conf.split("location ~* ^/marp(", 1)[1].split("}", 1)[0]
    assert "proxy_read_timeout 180s;" in marp_block
    assert "proxy_send_timeout 180s;" in marp_block
    view_block = conf.split("location ~* ^/view(", 1)[1].split("}", 1)[0]
    assert "proxy_read_timeout" not in view_block


def test_config_hash_is_reported(tmp_path):
    import hashlib
    result, _ = _check(tmp_path)
    expected = hashlib.sha256((tmp_path / "synctx.yaml").read_bytes()).hexdigest()
    assert f"설정 해시: {expected}" in result.stdout


def test_drawio_route_when_enabled(tmp_path):
    _, conf = _check(tmp_path, SYNCTX_SERVICES="viewer,cdn,marp,slides,drawio")
    assert "set $synctx_upstream http://drawio:8080;" in conf


def test_service_names_are_normalized(tmp_path):
    result, conf = _check(tmp_path, SYNCTX_SERVICES="' Viewer , CDN ,viewer'")
    assert result.returncode == 0, result.stdout + result.stderr
    assert conf.count("location ~* ^/view(") == 1
    assert "^/cdn(" in conf


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"SYNCTX_IMAGE_TAG": "latest"}, "latest"),
        ({"SYNCTX_IMAGE_TAG": None}, "SYNCTX_IMAGE_TAG"),
        ({"SYNCTX_SERVICES": "viewer,wiki"}, "wiki"),
        ({"SYNCTX_SERVICES": ""}, "켜진 서비스가 없습니다"),
        ({"SYNCTX_SERVICES": "viewer,cdn,sync"}, "2단계"),
        ({"SYNCTX_SLIDES_REPO_URL": None}, "SYNCTX_SLIDES_REPO_URL"),
        ({"SYNCTX_CONFIG_FILE": "/nonexistent/synctx.yaml"}, "SYNCTX_CONFIG_FILE"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "yes"}, "SYNCTX_TRUSTED_AUTH_PROXY"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1"}, "SYNCTX_TRUSTED_PROXY_CIDRS"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "proxy.internal"}, "proxy.internal"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "0.0.0.0/0"}, "모든 주소"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "10.0.0.0/0"}, "모든 주소"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "0.0.0.0/00"}, "모든 주소"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "0::/0"}, "모든 주소"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "999.1.1.1"}, "형식이 잘못됐습니다"),
        ({"SYNCTX_TRUSTED_AUTH_PROXY": "true", "SYNCTX_BIND_ADDRESS": "127.0.0.1", "SYNCTX_TRUSTED_PROXY_CIDRS": "10.0.0.0/33"}, "형식이 잘못됐습니다"),
        ({"SYNCTX_TLS_CERT": "cert.pem"}, "SYNCTX_TLS_KEY"),
        ({"SYNCTX_MARP_PROXY_TIMEOUT": "3m"}, "SYNCTX_MARP_PROXY_TIMEOUT"),
        ({"SYNCTX_MARP_PROXY_TIMEOUT": "180s"}, "SYNCTX_MARP_PROXY_TIMEOUT"),
        ({"SYNCTX_MARP_PROXY_TIMEOUT": "0"}, "SYNCTX_MARP_PROXY_TIMEOUT"),
    ],
)
def test_invalid_options_fail(tmp_path, overrides, message):
    result, _ = _check(tmp_path, **overrides)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr


def test_slides_without_marp_warns(tmp_path):
    result, _ = _check(tmp_path, SYNCTX_SERVICES="viewer,cdn,slides")
    assert result.returncode == 0
    assert "Marp가 꺼져 있습니다" in result.stdout + result.stderr


def test_tls_renders_https_server(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "cert.pem").write_text("c")
    (tls / "key.pem").write_text("k")
    result, conf = _check(tmp_path, SYNCTX_TLS_DIR=str(tls), SYNCTX_TLS_CERT="cert.pem", SYNCTX_TLS_KEY="key.pem")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "listen 443 ssl;" in conf
    assert "ssl_certificate /etc/nginx/tls/cert.pem;" in conf
    assert "ssl_certificate_key /etc/nginx/tls/key.pem;" in conf
    assert "return 301 https://$host$request_uri;" in conf


def test_tls_missing_file_fails(tmp_path):
    result, _ = _check(tmp_path, SYNCTX_TLS_DIR=str(tmp_path), SYNCTX_TLS_CERT="cert.pem", SYNCTX_TLS_KEY="key.pem")
    assert result.returncode != 0
    assert "cert.pem" in result.stdout + result.stderr


def test_quoted_extra_compose_files_are_sourced(tmp_path):
    # 옵션 파일은 bash가 source하므로 공백이 든 값은 큰따옴표로 감싸야 한다
    result, _ = _check(tmp_path, SYNCTX_EXTRA_COMPOSE_FILES='"a.yaml b.yaml"')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "command not found" not in result.stdout + result.stderr


def test_relative_env_file_is_not_looked_up_in_path(tmp_path):
    _write_env(tmp_path, {})
    decoy = tmp_path / "bin"
    decoy.mkdir()
    (decoy / "synctx.env").write_text("SYNCTX_IMAGE_TAG=latest\n")
    env = _clean_env(tmp_path)
    env["PATH"] = f"{decoy}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(DEPLOY), "--env-file", "synctx.env", "--secrets-file", "none.env", "--check"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "generated" / "gateway.check.conf").exists()


NGINX_IMAGE = "nginx:1.25.4-alpine"


def _docker_available():
    return os.environ.get("SYNCTX_TEST_NO_DOCKER") != "1" and shutil.which("docker") is not None


def _nginx_image_available():
    if not _docker_available():
        return False
    return subprocess.run(["docker", "image", "inspect", NGINX_IMAGE], capture_output=True).returncode == 0


def test_no_docker_option_blocks_image_inspection(monkeypatch):
    monkeypatch.setenv("SYNCTX_TEST_NO_DOCKER", "1")
    monkeypatch.setattr(shutil, "which", lambda name: "/test/docker")

    def unexpected_call(*args, **kwargs):
        pytest.fail("Docker 제외 옵션에서 subprocess를 호출했습니다")

    monkeypatch.setattr(subprocess, "run", unexpected_call)
    assert not _docker_available()
    assert not _nginx_image_available()


def _self_signed_pair(tls_dir, name):
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=synctx.test",
         "-keyout", str(tls_dir / f"{name}.key"), "-out", str(tls_dir / f"{name}.crt")],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(not _nginx_image_available(), reason=f"Docker 테스트 비활성 또는 docker/{NGINX_IMAGE} 없음")
@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 미설치")
@pytest.mark.parametrize("scenario", ["default", "drawio", "trusted", "tls"])
def test_generated_conf_passes_nginx_t(tmp_path, scenario):
    overrides = {}
    mounts = []
    if scenario == "drawio":
        overrides["SYNCTX_SERVICES"] = "viewer,cdn,marp,slides,drawio"
    elif scenario == "trusted":
        overrides.update(SYNCTX_TRUSTED_AUTH_PROXY="true", SYNCTX_TRUSTED_PROXY_CIDRS="10.1.2.3,fd00::/64", SYNCTX_BIND_ADDRESS="10.0.0.5")
    elif scenario == "tls":
        tls = tmp_path / "tls"
        tls.mkdir()
        _self_signed_pair(tls, "gw")
        overrides.update(SYNCTX_TLS_DIR=str(tls), SYNCTX_TLS_CERT="gw.crt", SYNCTX_TLS_KEY="gw.key", SYNCTX_HTTPS_PORT="8443")
        mounts = ["-v", f"{tls}:/etc/nginx/tls:ro"]
    result, _ = _check(tmp_path, **overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    conf = tmp_path / "generated" / "gateway.check.conf"
    nginx = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{conf}:/etc/nginx/conf.d/default.conf:ro", *mounts, NGINX_IMAGE, "nginx", "-t"],
        capture_output=True, text=True,
    )
    assert nginx.returncode == 0, nginx.stderr


FAKE_DOCKER = """#!/bin/bash
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
    run) cat "$FAKE_NGINX_OUTPUT"; exit "$FAKE_NGINX_RC" ;;
    compose)
        case " $* " in
            *" version "*) exit 0 ;;
            *" up "*) exit 1 ;;
        esac
        exit 0 ;;
esac
exit 0
"""


def _deploy_with_fake_docker(tmp_path, nginx_rc, nginx_output="", **overrides):
    env_file = _write_env(tmp_path, overrides)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    output = tmp_path / "nginx-output.txt"
    output.write_text(nginx_output)
    log = tmp_path / "docker.log"
    env = _clean_env(tmp_path)
    env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_DOCKER_LOG=str(log), FAKE_NGINX_OUTPUT=str(output), FAKE_NGINX_RC=str(nginx_rc))
    result = subprocess.run(
        ["bash", str(DEPLOY), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env")],
        capture_output=True, text=True, env=env,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_deploy_stops_before_up_when_nginx_rejects_conf(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    live = generated / "gateway.conf"
    live.write_text("# live config\n")
    result, calls = _deploy_with_fake_docker(tmp_path, 1, "nginx: [emerg] SSL_CTX_use_PrivateKey() failed (key values mismatch)\n")
    assert result.returncode == 1
    assert "key values mismatch" in result.stdout + result.stderr
    assert live.read_text() == "# live config\n"
    assert not [c for c in calls if " up " in f" {c} "]
    run = next(c for c in calls if c.startswith("run "))
    assert "nginx -t" in run
    assert f"{generated}/gateway.candidate.conf:/etc/nginx/conf.d/default.conf:ro" in run


def test_deploy_validates_candidate_before_up_and_replaces_live_conf(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    live = generated / "gateway.conf"
    live.write_text("# live config\n")
    inode = live.stat().st_ino
    result, calls = _deploy_with_fake_docker(tmp_path, 0)
    # 가짜 docker의 up은 실패하므로 배포는 실패로 끝나지만 그 전에 설정이 바뀌어 있어야 한다
    assert result.returncode == 1
    run_index = next(i for i, c in enumerate(calls) if c.startswith("run "))
    up_index = next(i for i, c in enumerate(calls) if " up " in f" {c} ")
    assert run_index < up_index
    assert "location ~* ^/view(" in live.read_text()
    assert live.stat().st_ino == inode
    assert not (generated / "gateway.candidate.conf").exists()


def test_deploy_mounts_tls_dir_for_validation(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "cert.pem").write_text("c")
    (tls / "key.pem").write_text("k")
    _, calls = _deploy_with_fake_docker(tmp_path, 1, SYNCTX_TLS_DIR=str(tls), SYNCTX_TLS_CERT="cert.pem", SYNCTX_TLS_KEY="key.pem")
    run = next(c for c in calls if c.startswith("run "))
    assert f"{tls}:/etc/nginx/tls:ro" in run


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
def test_compose_file_is_valid(tmp_path):
    env_file = _write_env(tmp_path, {"SYNCTX_SERVICES": "viewer,cdn,marp,slides,drawio"})
    override = tmp_path / "override.yaml"
    override.write_text("services:\n  viewer:\n    environment:\n      EXTRA: \"1\"\n")
    profiles = [arg for svc in ["viewer", "cdn", "marp", "slides", "drawio"] for arg in ("--profile", svc)]
    for files in (
        ["-f", str(COMPOSE)],
        ["-f", str(COMPOSE), "-f", str(COMPOSE.with_name("compose.tls.yaml"))],
        ["-f", str(COMPOSE), "-f", str(override)],
    ):
        result = subprocess.run(
            ["docker", "compose", *files, "--env-file", str(env_file), *profiles, "config", "-q"],
            capture_output=True, text=True, env=_clean_env(tmp_path),
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
def test_compose_carries_config_hash_label(tmp_path):
    env_file = _write_env(tmp_path, {})
    env = {**_clean_env(tmp_path), "SYNCTX_CONFIG_HASH": "abc123"}
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), "--profile", "viewer", "config"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "synctx.config-hash: abc123" in result.stdout


WRAPPER = ROOT / "scripts" / "docker" / "compose.sh"


def test_compose_wrapper_requires_command(tmp_path):
    env_file = _write_env(tmp_path, {})
    result = subprocess.run(["bash", str(WRAPPER), "--env-file", str(env_file)], capture_output=True, text=True, env=_clean_env(tmp_path))
    assert result.returncode != 0
    assert "docker compose에 넘길 명령" in result.stdout + result.stderr


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
def test_compose_wrapper_uses_enabled_profiles(tmp_path):
    env_file = _write_env(tmp_path, {"SYNCTX_SERVICES": "viewer,cdn,slides"})
    result = subprocess.run(
        ["bash", str(WRAPPER), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env"), "--", "config", "--services"],
        capture_output=True, text=True, env=_clean_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {"gateway", "viewer", "cdn", "slides", "slides-git"}


RENDER_NAMES = ("SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS")


@pytest.mark.parametrize("name", RENDER_NAMES)
@pytest.mark.parametrize("scenario", ["both", "missing", "directory", "colon"])
def test_deploy_rejects_invalid_render_files(tmp_path, name, scenario):
    key = tmp_path / ("key:bad" if scenario == "colon" else "key")
    if scenario == "directory":
        key.mkdir()
    elif scenario != "missing":
        key.write_text("file-secret-never-print" * 2)
    options = {name + "_FILE": shlex.quote(str(key))}
    if scenario == "both":
        options[name] = "direct-secret-never-print" * 2
    result, conf = _check(tmp_path, **options)
    assert result.returncode != 0
    assert name + "_FILE" in result.stdout + result.stderr
    assert "secret-never-print" not in result.stdout + result.stderr
    assert not conf


@pytest.mark.parametrize("files", [False, True])
def test_deploy_render_secrets_do_not_enter_output_config_or_hash(tmp_path, files):
    import hashlib

    options = {}
    for name in RENDER_NAMES:
        if files:
            key = tmp_path / name
            key.write_text(name + "-never-print" * 3)
            options[name + "_FILE"] = str(key)
        else:
            options[name] = name + "-never-print" * 3
    result, calls = _deploy_with_fake_docker(tmp_path, 0, **options)
    assert any(" up " in f" {call} " for call in calls)
    output = result.stdout + result.stderr + "\n".join(calls)
    assert "never-print" not in output
    generated = tmp_path / "generated"
    assert "never-print" not in "".join(path.read_text() for path in generated.iterdir() if path.is_file())
    config = tmp_path / "synctx.yaml"
    assert "never-print" not in config.read_text()
    expected = hashlib.sha256(config.read_bytes()).hexdigest()
    assert f"설정 해시: {expected}" in result.stdout
    for name in RENDER_NAMES:
        if files:
            (tmp_path / name).write_text("changed-key-never-print" * 3)
        else:
            options[name] = "changed-key-never-print" * 3
    changed, _ = _check(tmp_path, **options)
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert f"설정 해시: {expected}" in changed.stdout


def _compose_render_config(tmp_path, options):
    env_file = _write_env(tmp_path, options)
    result = subprocess.run(
        ["bash", str(WRAPPER), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env"),
         "--", "config", "--format", "json"],
        capture_output=True, text=True, env=_clean_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["viewer"]


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
def test_compose_null_render_keys_match_bare_name_passthrough_when_unset(tmp_path):
    viewer = _compose_render_config(tmp_path, {})
    names = [name + suffix for name in RENDER_NAMES for suffix in ("", "_FILE")]
    for name in names:
        assert viewer["environment"].get(name) is None
        assert viewer["environment"].get(name) != ""
    compose = yaml.safe_load(COMPOSE.read_text())
    compose["services"]["viewer"]["environment"] = [
        name if value is None else f"{name}={value}"
        for name, value in compose["services"]["viewer"]["environment"].items()
    ]
    comparison = tmp_path / "bare-names.yaml"
    comparison.write_text(yaml.safe_dump(compose))
    result = subprocess.run(
        ["docker", "compose", "-f", str(comparison), "--env-file", str(tmp_path / "synctx.env"),
         "--profile", "viewer", "config", "--format", "json"],
        capture_output=True, text=True, env=_clean_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["services"]["viewer"]["environment"] == viewer["environment"]


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
def test_compose_passes_current_and_previous_direct_keys(tmp_path):
    options = {name: name + "-test-key" * 4 for name in RENDER_NAMES}
    viewer = _compose_render_config(tmp_path, options)
    for name in RENDER_NAMES:
        assert viewer["environment"][name] == options[name]
        assert viewer["environment"].get(name + "_FILE") is None


@pytest.mark.skipif(not _docker_available(), reason="Docker 테스트 비활성 또는 docker 미설치")
@pytest.mark.parametrize("previous", [False, True])
def test_compose_mounts_render_key_files_and_remaps_environment(tmp_path, previous):
    options = {}
    mounts = {}
    for name, filename in zip(RENDER_NAMES, ("synctx-render-secret", "synctx-render-secret-previous")):
        if name.endswith("PREVIOUS") and not previous:
            continue
        key = tmp_path / (filename + " with spaces")
        key.write_text("file-key-never-serialize" * 2)
        options[name + "_FILE"] = shlex.quote(str(key))
        mounts[name] = (key, "/run/secrets/" + filename)
    viewer = _compose_render_config(tmp_path, options)
    for name, (host, target) in mounts.items():
        assert viewer["environment"][name + "_FILE"] == target
        assert viewer["environment"].get(name) is None
        mount = next(v for v in viewer["volumes"] if v["target"] == target)
        assert mount["source"] == str(host)
        assert mount["read_only"] is True
    if not previous:
        assert viewer["environment"].get("SYNCTX_RENDER_SECRET_PREVIOUS_FILE") is None
    assert "file-key-never-serialize" not in json.dumps(viewer)


def _jitsi_options(tmp_path, **extra):
    config = tmp_path / "jitsi-config.yaml"
    config.write_text("collab:\n  jitsi:\n    enabled: true\n    public_url: https://meet.synctx.local\n    app_id: synctx\n")
    tls = tmp_path / "jitsi-tls"
    tls.mkdir()
    for name in ("cert.pem", "key.pem"):
        (tls / name).write_text("test")
    options = dict(SYNCTX_JITSI="true", SYNCTX_JITSI_HOST="meet.synctx.local",
                   SYNCTX_JITSI_PUBLIC_URL="https://meet.synctx.local", SYNCTX_JITSI_ADVERTISE_IPS="192.0.2.1",
                   SYNCTX_CONFIG_FILE=str(config), SYNCTX_TLS_DIR=str(tls), SYNCTX_TLS_CERT="cert.pem", SYNCTX_TLS_KEY="key.pem",
                   JITSI_APP_SECRET="a" * 32, JITSI_ROOM_SECRET="r" * 32,
                   JITSI_JICOFO_PASSWORD="f" * 32, JITSI_JVB_PASSWORD="b" * 32)
    options.update(extra)
    return options


def test_jitsi_profile_and_matching_public_host_proxy(tmp_path):
    result, conf = _check(tmp_path, **_jitsi_options(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "server_name meet.synctx.local;" in conf
    assert "set $jitsi_upstream http://jitsi-web:80;" in conf
    assert "location /colibri-ws/jvb/" in conf
    assert "proxy_set_header Upgrade $http_upgrade;" in conf
    assert "proxy_set_header X-Forwarded-Proto https;" in conf
    assert "a" * 32 not in conf + result.stdout + result.stderr
    compose = yaml.safe_load(COMPOSE.read_text())
    for name in ("web", "prosody", "jicofo", "jvb"):
        service = compose["services"][f"jitsi-{name}"]
        assert service["profiles"] == ["jitsi"]
        assert "${SYNCTX_JITSI_IMAGE_TAG" in service["image"]
        assert "healthcheck" in service and "mem_limit" in service
        assert "/run" not in service["tmpfs"]
    services = compose["services"]
    assert services["jitsi-prosody"]["environment"]["AUTH_TYPE"] == "jwt"
    assert services["jitsi-prosody"]["environment"]["ENABLE_AUTO_OWNER"] == "0"
    assert services["jitsi-jicofo"]["environment"]["ENABLE_AUTH"] == "0"
    assert services["jitsi-jicofo"]["environment"]["ENABLE_AUTO_OWNER"] == "0"
    assert services["jitsi-web"]["environment"]["ENABLE_P2P"] == "false"
    bridge = services["jitsi-jvb"]
    assert bridge["environment"]["JVB_DISABLE_STUN"] == "1"
    assert "JICOFO_AUTH_PASSWORD" not in bridge["environment"]
    assert "JICOFO_AUTH_USER" not in bridge["environment"]
    assert "JWT_APP_ID" not in bridge["environment"]
    assert "JVB_AUTH_PASSWORD" not in services["jitsi-jicofo"]["environment"]
    assert len(bridge["ports"]) == 1
    assert ":10000:10000/udp" in compose["services"]["jitsi-jvb"]["ports"][0]
    assert "JITSI_ROOM_SECRET" not in str(compose["services"]["jitsi-prosody"])


@pytest.mark.parametrize("override", [None, "stable-9823-1"])
def test_jitsi_compose_tag_default_and_override(tmp_path, override):
    options = tmp_path / "options.env"
    options.write_text(f"SYNCTX_JITSI_IMAGE_TAG={override}\n" if override else "")
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("SYNCTX_", "JITSI_"))}
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; ENV_FILE="$2"; SECRETS_FILE="$3"; load_env_files; printf "tag=%s\\n" "$SYNCTX_JITSI_IMAGE_TAG"',
         "test", str(ROOT / "scripts/docker/_common.sh"), str(options), str(tmp_path / "missing.env")],
        capture_output=True, text=True, env=environment,
    )
    tag = override or yaml.safe_load((ROOT / "helm/synctx/values.yaml").read_text())["jitsi"]["imageTag"]
    assert result.returncode == 0, result.stderr
    assert f"tag={tag}\n" in result.stdout


@pytest.mark.parametrize("overrides", [
    {"SYNCTX_JITSI_IMAGE_TAG": "latest"}, {"SYNCTX_JITSI_HOST": ""},
    {"SYNCTX_JITSI_PUBLIC_URL": "https://other"}, {"SYNCTX_TLS_CERT": None},
    {"SYNCTX_JITSI_ADVERTISE_IPS": ""}, {"JITSI_JVB_PASSWORD": None},
    {"SYNCTX_JITSI_ADVERTISE_IPS": "192.0.2.1#10000"},
    {"SYNCTX_JITSI_ADVERTISE_IPS": "meet.synctx.local"},
    {"SYNCTX_JITSI_ADVERTISE_IPS": "192.0.2.1,"},
    {"JITSI_JICOFO_PASSWORD": "f" * 31}, {"JITSI_JVB_PASSWORD": "b" * 31},
    {"JITSI_APP_SECRET": "short"}, {"JITSI_ROOM_SECRET": "a" * 32},
    {"SYNCTX_SERVICES": "cdn"},
])
def test_jitsi_invalid_settings_rejected(tmp_path, overrides):
    result, _ = _check(tmp_path, **_jitsi_options(tmp_path, **overrides))
    assert result.returncode != 0


def test_jitsi_rejects_config_mismatch(tmp_path):
    options = _jitsi_options(tmp_path)
    Path(options["SYNCTX_CONFIG_FILE"]).write_text("collab: {jitsi: {enabled: false}}\n")
    result, _ = _check(tmp_path, **options)
    assert result.returncode != 0


def test_jitsi_current_previous_files_are_mounted_without_printing(tmp_path):
    options = _jitsi_options(tmp_path)
    for slot in ("JITSI_APP_SECRET", "JITSI_ROOM_SECRET"):
        options.pop(slot)
        for suffix in ("", "_PREVIOUS"):
            key = tmp_path / (slot + suffix)
            key.write_text(("a" if slot == "JITSI_APP_SECRET" else "r") * 32 + "\r\n")
            options[slot + suffix + "_FILE"] = str(key)
    result, conf = _check(tmp_path, **options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "a" * 32 not in result.stdout + result.stderr + conf
    viewer = yaml.safe_load(COMPOSE.read_text())["services"]["viewer"]
    for slot in ("JITSI_APP_SECRET", "JITSI_ROOM_SECRET"):
        assert slot + "_FILE" in viewer["environment"]
        assert slot + "_PREVIOUS_FILE" in viewer["environment"]
        assert any(slot + "_FILE_MOUNT" in mount for mount in viewer["volumes"])
