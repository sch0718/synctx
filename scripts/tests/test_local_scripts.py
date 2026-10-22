"""scripts/local 스크립트·values-local.yaml·scripts/test-all.sh 정합성 테스트."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "scripts" / "local"
DEPLOY_DEPS = LOCAL / "steps" / "deploy-deps.sh"
VALUES_LOCAL = ROOT / "helm" / "synctx" / "values-local.yaml"
TEST_ALL = ROOT / "scripts" / "test-all.sh"

FAKE_LOGGER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case " $* " in
    *" -f - "*) cat >> "$FAKE_LOG" ;;
esac
exit "${FAKE_EXIT:-0}"
"""


def _fake_bin(tmp_path, *names):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        path = bin_dir / name
        path.write_text(FAKE_LOGGER)
        path.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_LOG"] = str(tmp_path / "calls.log")
    return env


def _mailpit_manifests():
    text = DEPLOY_DEPS.read_text()
    body = text.split("<<'MAIL_EOF'\n", 1)[1].split("\nMAIL_EOF", 1)[0]
    return {doc["kind"]: doc for doc in yaml.safe_load_all(body) if doc}


def _deps_namespace():
    match = re.search(r'^DEPS_NAMESPACE="([^"]+)"', (LOCAL / "_common.sh").read_text(), re.M)
    return match.group(1)


def _values_local():
    values = yaml.safe_load(VALUES_LOCAL.read_text())
    return values, yaml.safe_load(values["synctxConfig"]["data"])


def test_values_local_smtp_points_to_mailpit_service():
    service = _mailpit_manifests()["Service"]
    smtp_port = next(p["port"] for p in service["spec"]["ports"] if p["name"] == "smtp")
    _, config = _values_local()
    mail = config["outbound_feedback"]["outbound_mail"]
    assert mail["smtp_host"] == f"{service['metadata']['name']}.{_deps_namespace()}.svc.cluster.local"
    assert mail["smtp_port"] == smtp_port
    assert "mailhog" not in VALUES_LOCAL.read_text().lower()


def test_mailpit_accepts_feedback_smtp_login():
    # mailer는 자격 증명이 있으면 로그인하므로 Mailpit이 평문 AUTH를 받아야 메일이 나간다
    values, _ = _values_local()
    assert values["feedback"]["secrets"]["smtpCredential"]
    container = _mailpit_manifests()["Deployment"]["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container.get("env", [])}
    assert env.get("MP_SMTP_AUTH_ACCEPT_ANY") == "true"
    assert env.get("MP_SMTP_AUTH_ALLOW_INSECURE") == "true"


def _deploy_deps(tmp_path, *args):
    env = _fake_bin(tmp_path, "kubectl", "minikube", "docker")
    return subprocess.run(
        ["bash", str(DEPLOY_DEPS), *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL
    )


@pytest.mark.parametrize("flag", ["--skip-mailpit", "--skip-mailhog"])
def test_deploy_deps_skip_mailpit(tmp_path, flag):
    result = _deploy_deps(tmp_path, "--skip-xwiki", "--skip-op", "--skip-images", flag)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Mailpit 배포" not in result.stdout
    assert "mailpit" not in (tmp_path / "calls.log").read_text()
    assert ("--skip-mailpit" in result.stdout) == (flag == "--skip-mailhog")


def test_deploy_deps_help_lists_mailpit_flag(tmp_path):
    result = _deploy_deps(tmp_path, "--help")
    assert result.returncode == 0
    assert "--skip-mailpit" in result.stdout
    assert "--skip-mailhog" not in result.stdout


def test_deploy_synctx_without_values_points_to_existing_files(tmp_path):
    shutil.copytree(LOCAL, tmp_path / "scripts" / "local")
    env = _fake_bin(tmp_path, "kubectl", "helm")
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / "local" / "steps" / "deploy-synctx.sh")],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 1
    paths = set(re.findall(r"[\w.-]+(?:/[\w.-]+)+\.(?:ya?ml|md)", result.stdout))
    assert "helm/synctx/values-local.yaml" in paths
    for path in paths:
        assert (ROOT / path).is_file(), path
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "helm/synctx/values-local.yaml"], cwd=ROOT, capture_output=True
    )
    assert tracked.returncode == 0


def _test_all(tmp_path, *args, uv_exit=0):
    env = _fake_bin(tmp_path, "uv", "node")
    (tmp_path / "bin" / "uv").write_text(FAKE_LOGGER.replace("${FAKE_EXIT:-0}", str(uv_exit)))
    result = subprocess.run(["bash", str(TEST_ALL), *args], capture_output=True, text=True, env=env)
    calls = (tmp_path / "calls.log").read_text().splitlines()
    return result, calls


def test_test_all_covers_every_test_dir(tmp_path):
    result, calls = _test_all(tmp_path, "-x")
    assert result.returncode == 0, result.stdout + result.stderr
    uv_args, node_args = calls[0].split(), calls[1].split()
    assert uv_args[:4] == ["run", "--frozen", "--all-packages", "pytest"]
    expected = sorted(
        str(p.relative_to(ROOT)) for p in [*ROOT.glob("shared/*/tests"), *ROOT.glob("services/*/tests")]
    ) + ["scripts/tests"]
    assert {"services/feedback/tests", "services/slides/tests"} <= set(expected)
    assert sorted(a for a in uv_args if a.endswith("/tests")) == sorted(expected)
    assert uv_args[-2:] == ["-q", "-x"]
    js_tests = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("services/viewer/tests/js/*.test.mjs"))
    assert js_tests
    assert node_args == ["--test", *js_tests]


def test_test_all_runs_node_even_if_pytest_fails(tmp_path):
    result, calls = _test_all(tmp_path, uv_exit=1)
    assert result.returncode == 1
    assert calls[1].startswith("--test ")
    assert "SOME TESTS FAILED" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make 미설치")
def test_make_test_calls_test_all():
    result = subprocess.run(["make", "-n", "test"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bash scripts/test-all.sh"


def _source_common(tmp_path, command, **extra_env):
    env = _fake_bin(tmp_path, "kubectl", "minikube", "helm")
    env.pop("MINIKUBE_PROFILE", None)
    env.update(extra_env)
    script = f'source "{LOCAL / "_common.sh"}"; {command}'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return Path(env["FAKE_LOG"]).read_text().splitlines()


@pytest.mark.parametrize(
    ("extra_env", "profile"),
    [({}, "minikube"), ({"MINIKUBE_PROFILE": "synctx"}, "synctx")],
)
def test_common_targets_selected_minikube_profile(tmp_path, extra_env, profile):
    calls = _source_common(
        tmp_path,
        "minikube status; kubectl get pods -n synctx; helm list -n synctx",
        **extra_env,
    )
    assert calls == [
        f"-p {profile} status",
        f"--context {profile} get pods -n synctx",
        f"--kube-context {profile} list -n synctx",
    ]


def test_start_minikube_creates_selected_profile(tmp_path):
    env = _fake_bin(tmp_path, "minikube")
    fake = tmp_path / "bin" / "minikube"
    fake.write_text(FAKE_LOGGER.replace('exit "${FAKE_EXIT:-0}"', 'case " $* " in *" status "*) exit 1 ;; esac'))
    env.update(MINIKUBE_PROFILE="synctx", MINIKUBE_MEMORY="8192")
    script = f'source "{LOCAL / "_common.sh"}"; start_minikube'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    calls = Path(env["FAKE_LOG"]).read_text().splitlines()
    assert calls[0] == "-p synctx status"
    assert calls[1].startswith("-p synctx start ") and "--memory=8192" in calls[1]


def test_prerequisites_check_ignores_wrapper_functions(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("bash", "dirname", "grep", "sed", "cat", "printf"):
        target = shutil.which(name)
        if target:
            (bin_dir / name).symlink_to(target)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    script = f'source "{LOCAL / "_common.sh"}"; check_prerequisites minikube helm'
    result = subprocess.run([shutil.which("bash"), "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "minikube" in result.stdout + result.stderr



def test_openproject_allows_host_synctx_config_uses():
    body = DEPLOY_DEPS.read_text().split("<<'OP_EOF'\n", 1)[1].split("\nOP_EOF", 1)[0]
    deploy = next(d for d in yaml.safe_load_all(body) if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "openproject")
    env = {e["name"]: e.get("value") for e in deploy["spec"]["template"]["spec"]["containers"][0]["env"]}
    _, config = _values_local()
    host = config["targets"]["openproject"]["base_url"].split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    assert host in yaml.safe_load(env["OPENPROJECT_ADDITIONAL__HOST__NAMES"])
