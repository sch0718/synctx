"""scripts/docker/deploy.sh --check 테스트(검사·게이트웨이 설정 생성)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "docker" / "deploy.sh"
COMPOSE = ROOT / "deploy" / "docker" / "compose.yaml"


def _write_env(tmp_path, overrides):
    config = tmp_path / "synctx.yaml"
    config.write_text("authz:\n  enabled: false\n")
    values = {
        "SYNCTX_IMAGE_TAG": "0.1.0-abc1234",
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


def _nginx_image_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", NGINX_IMAGE], capture_output=True).returncode == 0


def _self_signed_pair(tls_dir, name):
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=synctx.test",
         "-keyout", str(tls_dir / f"{name}.key"), "-out", str(tls_dir / f"{name}.crt")],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(not _nginx_image_available(), reason=f"docker 또는 {NGINX_IMAGE} 이미지 없음")
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker 미설치")
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker 미설치")
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker 미설치")
def test_compose_wrapper_uses_enabled_profiles(tmp_path):
    env_file = _write_env(tmp_path, {"SYNCTX_SERVICES": "viewer,cdn,slides"})
    result = subprocess.run(
        ["bash", str(WRAPPER), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env"), "--", "config", "--services"],
        capture_output=True, text=True, env=_clean_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {"gateway", "viewer", "cdn", "slides", "slides-git"}
