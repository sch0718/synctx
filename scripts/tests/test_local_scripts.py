"""scripts/local 스크립트·values-local.yaml·scripts/test-all.sh 정합성 테스트."""

import json
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


def test_verify_reports_summary_when_kubectl_fails(tmp_path):
    env = _fake_bin(tmp_path, "minikube", "kubectl", "curl")
    (tmp_path / "bin" / "kubectl").write_text("#!/bin/sh\nexit 2\n")
    (tmp_path / "bin" / "curl").write_text("#!/bin/sh\nprintf 200\n")
    result = subprocess.run(
        ["bash", str(LOCAL / "verify.sh")], capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 1
    assert "검증 결과" in result.stdout
    assert "항목에서 문제 발견" in result.stdout


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


SETUP_MATTERMOST = LOCAL / "steps" / "setup-mattermost.sh"
VERIFY_COLLAB = LOCAL / "steps" / "verify-collab.sh"
COLLAB_SECRET = "values-local.collab.secret.yaml"


def _mattermost_manifests(tmp_path):
    result = _deploy_deps(tmp_path, "--skip-xwiki", "--skip-op", "--skip-mailpit", "--skip-images", "--mattermost")
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "calls.log").read_text()
    manifest = log.rsplit("-f -\n", 1)[1].split("\n--context", 1)[0]
    return result, [doc for doc in yaml.safe_load_all(manifest) if doc]


def _deploy_mattermost_env():
    text = DEPLOY_DEPS.read_text()
    match = re.search(r"ALLOWEDUNTRUSTEDINTERNALCONNECTIONS\n\s+value: \"([^\"]+)\"", text)
    return match.group(1).split(",")


def test_values_local_gateway_matches_local_mattermost(tmp_path):
    from synctx_common.config import SynctxConfig

    _, manifests = _mattermost_manifests(tmp_path)
    services = {m["metadata"]["name"]: m for m in manifests if m["kind"] == "Service"}
    port = services["mattermost"]["spec"]["ports"][0]["port"]
    values, config = _values_local()
    gateway = SynctxConfig.model_validate(config).collab.gateway
    assert gateway.mattermost.url == f"http://mattermost.{_deps_namespace()}.svc.cluster.local:{port}"
    policy = values["collabGateway"]["networkPolicy"]["mattermost"]
    assert policy == {"namespace": _deps_namespace(), "port": port}
    setup = SETUP_MATTERMOST.read_text()
    team = re.search(r'TEAM="\$\{MATTERMOST_TEAM:-([^}]+)\}"', setup).group(1)
    channel = re.search(r'CHANNELS=\("([a-z][a-z0-9_-]*)"\)', setup).group(1)
    assert gateway.channels[channel].team == team and gateway.channels[channel].channel == channel
    assert values["collabGateway"]["enabled"] is False


def test_mattermost_is_opt_in_and_pinned(tmp_path):
    off = _deploy_deps(tmp_path, "--skip-xwiki", "--skip-op", "--skip-mailpit", "--skip-images")
    assert off.returncode == 0 and "Mattermost" not in off.stdout
    assert "mattermost" not in (tmp_path / "calls.log").read_text()
    on_dir = tmp_path / "on"
    on_dir.mkdir()
    _, manifests = _mattermost_manifests(on_dir)
    deployments = {m["metadata"]["name"]: m for m in manifests if m["kind"] == "Deployment"}
    assert set(deployments) == {"mattermost", "mattermost-db"}
    image = deployments["mattermost"]["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image.startswith("mattermost/mattermost-team-edition:") and not image.endswith(":latest")
    env = {e["name"]: e["value"] for e in deployments["mattermost"]["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["MM_SERVICESETTINGS_ENABLELOCALMODE"] == "true" and env["MM_SERVICESETTINGS_ENABLEBOTACCOUNTCREATION"] == "true"
    assert "@mattermost-db:5432/" in env["MM_SQLSETTINGS_DATASOURCE"]


def test_deploy_deps_help_lists_mattermost_flag(tmp_path):
    assert "--mattermost" in _deploy_deps(tmp_path, "--help").stdout


def test_load_image_pulls_with_platform_override(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "minikube").write_text(
        "#!/bin/sh\ncase \"$*\" in\n *ssh*) echo aarch64;;\n *'image pull'*) exit 1;;\n *'image ls'*) exit 0;;\n *) exit 0;;\nesac\n")
    (bin_dir / "docker").write_text(FAKE_LOGGER)
    for name in ("minikube", "docker"):
        (bin_dir / name).chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FAKE_LOG=str(tmp_path / "calls.log"))
    script = f'source "{LOCAL}/_common.sh"; load_image img:1; LOAD_IMAGE_PLATFORM=linux/amd64 load_image img:2'
    subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    calls = (tmp_path / "calls.log").read_text()
    assert "pull --platform linux/arm64 img:1" in calls
    assert "pull --platform linux/amd64 img:2" in calls
    assert "image pull img:2" not in calls


def test_collab_values_file_is_gitignored():
    result = subprocess.run(["git", "check-ignore", f"helm/synctx/{COLLAB_SECRET}"], cwd=ROOT, capture_output=True)
    assert result.returncode == 0


def _scripts_copy(tmp_path):
    shutil.copytree(LOCAL, tmp_path / "scripts" / "local")
    (tmp_path / "helm" / "synctx").mkdir(parents=True)
    shutil.copy(VALUES_LOCAL, tmp_path / "helm" / "synctx" / "values-local.yaml")
    return tmp_path


@pytest.mark.parametrize("present", [True, False])
def test_deploy_synctx_applies_collab_values_only_when_present(tmp_path, present):
    root = _scripts_copy(tmp_path)
    if present:
        (root / "helm" / "synctx" / COLLAB_SECRET).write_text("collabGateway: {enabled: true}\n")
    env = _fake_bin(tmp_path, "kubectl", "helm")
    result = subprocess.run(["bash", str(root / "scripts" / "local" / "steps" / "deploy-synctx.sh")],
                   capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert "Jitsi를 끕니다" in result.stdout
    helm_call = next(line for line in (tmp_path / "calls.log").read_text().splitlines() if "upgrade --install" in line)
    assert (COLLAB_SECRET in helm_call) == present


FAKE_UV_TOKEN = """#!/bin/sh
printf 'uv %s\\n' "$*" >> "$FAKE_LOG"
echo "${FAKE_TOKEN-SECRETBOTTOKEN}"
echo "${FAKE_COMMAND_TOKEN-SECRETCMDTOKEN}"
"""


def _setup_mattermost(tmp_path, token=None, *args, command_token=None):
    root = _scripts_copy(tmp_path)
    env = _fake_bin(tmp_path, "kubectl", "curl")
    (tmp_path / "bin" / "uv").write_text(FAKE_UV_TOKEN)
    (tmp_path / "bin" / "uv").chmod(0o755)
    env["HOME"] = str(tmp_path / "home")
    if token is not None:
        env["FAKE_TOKEN"] = token
    if command_token is not None:
        env["FAKE_COMMAND_TOKEN"] = command_token
    return root, subprocess.run(
        ["bash", str(root / "scripts" / "local" / "steps" / "setup-mattermost.sh"), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)


def test_setup_mattermost_writes_private_values_file(tmp_path):
    root, result = _setup_mattermost(tmp_path, None, "chan-a", "chan-b")
    assert result.returncode == 0, result.stdout + result.stderr
    path = root / "helm" / "synctx" / COLLAB_SECRET
    data = yaml.safe_load(path.read_text())["collabGateway"]
    assert data["enabled"] is True and data["secrets"]["create"] is True
    assert data["secrets"]["botToken"] == "SECRETBOTTOKEN"
    assert len(data["secrets"]["viewerServiceToken"]) == 48
    assert data["secrets"]["commandToken"] == "SECRETCMDTOKEN"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    calls = (tmp_path / "calls.log").read_text()
    for channel in ("chan-a", "chan-b"):
        assert f"channel create --team synctx --name {channel}" in calls
    uv_call = next(line for line in calls.splitlines() if line.startswith("uv "))
    assert "mattermost_bot.py" in uv_call and "--team synctx --bot synctx-bot" in uv_call and uv_call.endswith("chan-a chan-b")
    assert "--command-url http://synctx-collab-gateway.synctx.svc.cluster.local/api/collab/mattermost/command" in uv_call
    assert "SECRETBOTTOKEN" not in result.stdout and "SECRETCMDTOKEN" not in result.stdout
    password_file = tmp_path / "home" / ".cache" / "synctx" / "mattermost-admin-password"
    assert oct(password_file.stat().st_mode & 0o777) == "0o600"
    assert password_file.read_text().strip() not in result.stdout


def test_setup_mattermost_uses_default_channel_and_local_mode(tmp_path):
    _, result = _setup_mattermost(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls.log").read_text()
    assert "channel create --team synctx --name team-docs" in calls
    assert all("mmctl --local" in line for line in calls.splitlines() if "mmctl" in line)
    assert not any("bot create" in line or "token generate" in line for line in calls.splitlines())


@pytest.mark.parametrize("bot,command", [("", None), (None, "")])
def test_setup_mattermost_fails_without_a_token_and_writes_no_file(tmp_path, bot, command):
    root, result = _setup_mattermost(tmp_path, bot, command_token=command)
    assert result.returncode != 0
    assert not (root / "helm" / "synctx" / COLLAB_SECRET).exists()


def test_command_url_matches_gateway_service_and_endpoint():
    setup = SETUP_MATTERMOST.read_text()
    url = re.search(r'COMMAND_URL="\$\{SYNCTX_COMMAND_URL:-(.+)\}"', setup).group(1)
    assert url == "http://synctx-collab-gateway.${NAMESPACE}.svc.cluster.local/api/collab/mattermost/command"
    values, _ = _values_local()
    assert values["collabGateway"]["networkPolicy"]["mattermost"]["namespace"]
    gateway_app = (ROOT / "services" / "collab-gateway" / "src" / "app.py").read_text()
    assert 'COMMAND_PATH = "/api/collab/mattermost/command"' in gateway_app
    mattermost_env = [m for m in _deploy_mattermost_env()]
    assert "synctx-collab-gateway.${NAMESPACE}.svc.cluster.local" in mattermost_env and "host.minikube.internal" in mattermost_env


def test_setup_mattermost_requires_running_mattermost(tmp_path):
    root = _scripts_copy(tmp_path)
    env = _fake_bin(tmp_path, "docker")
    (tmp_path / "bin" / "kubectl").write_text("#!/bin/sh\ncase \"$*\" in *'get deploy/mattermost'*) exit 1;; esac\nexit 0\n")
    (tmp_path / "bin" / "kubectl").chmod(0o755)
    env["HOME"] = str(tmp_path / "home")
    result = subprocess.run(["bash", str(root / "scripts" / "local" / "steps" / "setup-mattermost.sh")],
                            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert result.returncode == 1 and "deploy-deps.sh --mattermost" in result.stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm 미설치")
def test_local_values_render_gateway_when_collab_file_is_applied(tmp_path):
    collab = tmp_path / COLLAB_SECRET
    collab.write_text("collabGateway:\n  enabled: true\n  secrets: {create: true, botToken: b, viewerServiceToken: v}\n")
    result = subprocess.run(["helm", "template", "synctx", str(ROOT / "helm" / "synctx"), "-f", str(VALUES_LOCAL), "-f", str(collab)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    kinds = {(d["kind"], d["metadata"]["name"]) for d in yaml.safe_load_all(result.stdout) if d}
    assert ("Deployment", "synctx-collab-gateway") in kinds
    policy = next(d for d in yaml.safe_load_all(result.stdout) if d and d["kind"] == "NetworkPolicy" and "collab" in d["metadata"]["name"])
    assert any(r["ports"][0]["port"] == 8065 for r in policy["spec"]["egress"])


def _bot_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mattermost_bot", LOCAL / "steps" / "mattermost_bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MattermostApi:
    def __init__(self, bot_exists=False, fail=None, commands=None):
        self.bot_exists = bot_exists
        self.fail = fail
        self.commands = commands or []
        self.calls = []

    def __call__(self, request):
        import httpx

        path, method = request.url.path, request.method
        self.calls.append((method, path, request.headers.get("Authorization")))
        if self.fail == path:
            return httpx.Response(500)
        if path == "/api/v4/users/login":
            return httpx.Response(200, headers={"Token": "session-1"}, json={"id": "admin"})
        if path == "/api/v4/teams/name/synctx":
            return httpx.Response(200, json={"id": "team1"})
        if "/channels/name/" in path:
            return httpx.Response(200, json={"id": "chan-" + path.rsplit("/", 1)[1]})
        if path == "/api/v4/users/username/synctx-bot":
            return httpx.Response(200, json={"id": "bot1"}) if self.bot_exists else httpx.Response(404)
        if path == "/api/v4/bots":
            return httpx.Response(201, json={"user_id": "bot1"})
        if path.endswith("/tokens"):
            return httpx.Response(200, json={"token": "bot-access-token"})
        if path == "/api/v4/commands" and method == "GET":
            assert request.url.params["team_id"] == "team1" and request.url.params["custom_only"] == "true"
            return httpx.Response(200, json=self.commands)
        if path == "/api/v4/commands" and method == "POST":
            self.created = json.loads(request.content)
            return httpx.Response(201, json={"id": "cmd-new", "token": "command-token-new"})
        if path.startswith("/api/v4/commands/") and method == "DELETE":
            return httpx.Response(200, json={"status": "OK"})
        return httpx.Response(200, json={})


def _provision(api, **kw):
    import httpx

    module = _bot_module()
    client = httpx.Client(transport=httpx.MockTransport(api), base_url="http://mm")
    args = dict(admin="admin", password="pw", team="synctx", channels=["a", "b"], bot="synctx-bot",
                command_url="http://gw/api/collab/mattermost/command")
    args.update(kw)
    return module, module.provision_bot(client, **args)


@pytest.mark.parametrize("bot_exists", [False, True])
def test_provision_bot_creates_or_reuses_and_returns_token(bot_exists):
    api = _MattermostApi(bot_exists=bot_exists)
    _, tokens = _provision(api)
    assert tokens == ("bot-access-token", "command-token-new")
    paths = [(m, p) for m, p, _ in api.calls]
    assert (("POST", "/api/v4/bots") in paths) != bot_exists
    assert ("POST", "/api/v4/teams/team1/members") in paths
    assert ("POST", "/api/v4/channels/chan-a/members") in paths and ("POST", "/api/v4/channels/chan-b/members") in paths
    assert ("POST", "/api/v4/users/bot1/tokens") in paths
    assert all(auth == "Bearer session-1" for _, p, auth in api.calls if p != "/api/v4/users/login")


def test_provision_creates_command_with_url_and_autocomplete():
    api = _MattermostApi()
    _provision(api)
    created = api.created
    assert created["trigger"] == "synctx" and created["method"] == "P" and created["team_id"] == "team1"
    assert created["url"] == "http://gw/api/collab/mattermost/command" and created["auto_complete"] is True
    assert "오늘" in created["auto_complete_hint"]
    assert "검색 <키워드> | 근거 <id>" in created["auto_complete_hint"]
    assert "참여 [id]" in created["auto_complete_hint"]


def test_provision_reuses_existing_command_with_same_url():
    existing = [{"id": "c1", "trigger": "synctx", "method": "P", "token": "old-token",
                 "url": "http://gw/api/collab/mattermost/command"}]
    api = _MattermostApi(commands=existing)
    _, tokens = _provision(api)
    assert tokens[1] == "old-token"
    assert not [c for c in api.calls if c[0] == "DELETE"] and not hasattr(api, "created")


def test_provision_recreates_command_when_url_differs_and_ignores_other_triggers():
    existing = [{"id": "other", "trigger": "else", "method": "P", "token": "t", "url": "http://x"},
                {"id": "c1", "trigger": "synctx", "method": "P", "token": "old", "url": "http://elsewhere"}]
    api = _MattermostApi(commands=existing)
    _, tokens = _provision(api)
    deletes = [p for m, p, _ in api.calls if m == "DELETE"]
    assert deletes == ["/api/v4/commands/c1"] and tokens[1] == "command-token-new"


def test_provision_bot_raises_without_leaking_secrets_on_failure():
    api = _MattermostApi(fail="/api/v4/bots")
    with pytest.raises(Exception) as exc:
        _provision(api)
    assert type(exc.value).__name__ == "ProvisionError" and "500" in str(exc.value) and "pw" not in str(exc.value) and "session-1" not in str(exc.value)


def test_bot_cli_prints_only_the_two_tokens(tmp_path, capsys, monkeypatch):
    import httpx

    module = _bot_module()
    password_file = tmp_path / "pw"
    password_file.write_text("pw\n")
    real_client = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(_MattermostApi()), **kw))
    code = module.main(["--base", "http://mm", "--admin", "admin", "--password-file", str(password_file),
                        "--team", "synctx", "--bot", "synctx-bot", "--command-url", "http://gw/c", "a"])
    out = capsys.readouterr()
    assert code == 0 and out.out == "bot-access-token\ncommand-token-new\n" and "pw" not in out.err


def test_bot_cli_reports_failure_on_stderr_with_exit_1(tmp_path, capsys, monkeypatch):
    import httpx

    module = _bot_module()
    password_file = tmp_path / "pw"
    password_file.write_text("pw\n")
    real_client = httpx.Client
    api = _MattermostApi(fail="/api/v4/teams/name/synctx")
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(api), **kw))
    code = module.main(["--base", "http://mm", "--admin", "admin", "--password-file", str(password_file),
                        "--team", "synctx", "--bot", "synctx-bot", "--command-url", "http://gw/c", "a"])
    out = capsys.readouterr()
    assert code == 1 and out.out == "" and "봇 준비 실패" in out.err


JITSI_SCRIPT = LOCAL / "steps" / "deploy-jitsi.sh"


def test_jitsi_scripts_parse_without_cluster_commands():
    for script in (JITSI_SCRIPT, LOCAL / "verify.sh"):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        help_result = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
        assert help_result.returncode == 0 and "사용법" in help_result.stdout
        unknown = subprocess.run(["bash", str(script), "--unknown"], capture_output=True, text=True)
        assert unknown.returncode != 0


def test_jitsi_is_opt_in_and_private_values_are_ignored():
    assert "jitsi" not in (LOCAL / "setup.sh").read_text().lower()
    assert "jitsi" not in (LOCAL / "steps" / "build.sh").read_text().lower()
    assert yaml.safe_load((ROOT / "helm/synctx/values.yaml").read_text())["jitsi"]["enabled"] is False
    for name in ("values-local.yaml", "values-production.yaml"):
        assert yaml.safe_load((ROOT / "helm/synctx" / name).read_text()).get("jitsi", {}).get("enabled", False) is False
    ignored = subprocess.run(["git", "check-ignore", "helm/synctx/values-local.jitsi.secret.yaml"], cwd=ROOT, capture_output=True)
    assert ignored.returncode == 0


def test_jitsi_blocks_running_mattermost_before_creating_secrets(tmp_path):
    root = _scripts_copy(tmp_path)
    env = _fake_bin(tmp_path, "kubectl", "minikube", "helm")
    fake = tmp_path / "bin" / "kubectl"
    fake.write_text("#!/bin/sh\necho 1\n")
    env["HOME"] = str(tmp_path / "home")
    result = subprocess.run(["bash", str(root / "scripts/local/steps/deploy-jitsi.sh"), "--skip-images"], capture_output=True, text=True, env=env)
    assert result.returncode != 0 and "Mattermost를 먼저 중지" in result.stdout
    assert not (root / "helm/synctx/values-local.jitsi.secret.yaml").exists()


@pytest.mark.parametrize("addresses", [None, "192.0.2.1", "192.0.2.1,198.51.100.2"])
def test_jitsi_generated_values_are_private_and_load_pinned_images(tmp_path, addresses):
    root = _scripts_copy(tmp_path)
    shutil.copy(ROOT / "helm/synctx/values.yaml", root / "helm/synctx/values.yaml")
    with (root / "scripts/local/_common.sh").open("a") as stream:
        stream.write('\nload_image() { printf "image %s\\n" "$1" >> "$FAKE_LOG"; }\n')
    env = _fake_bin(tmp_path, "kubectl", "helm", "minikube")
    (tmp_path / "bin" / "minikube").write_text('#!/bin/sh\necho 192.0.2.1\n')
    # uv executes only the inline configuration generator; cluster/image calls are fakes.
    uv = tmp_path / "bin" / "uv"
    import sys
    uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" -\n')
    uv.chmod(0o755)
    env["HOME"] = str(tmp_path / "home")
    args = ["--advertise-ips", addresses] if addresses else []
    result = subprocess.run(["bash", str(root / "scripts/local/steps/deploy-jitsi.sh"), *args], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    values_file = root / "helm/synctx/values-local.jitsi.secret.yaml"
    values = yaml.safe_load(values_file.read_text())
    assert values_file.stat().st_mode & 0o777 == 0o600
    assert values["jitsi"]["enabled"] is True
    assert values["jitsi"]["jvb"]["advertisedIPs"] == (addresses or "192.0.2.1")
    assert values["collabGateway"]["enabled"] is False
    assert values["jitsi"]["jwtSecret"]["current"] != values["jitsi"]["roomSecret"]["current"]
    assert values["jitsi"]["jwtSecret"]["current"] not in result.stdout + result.stderr
    assert "UDP 미디어는 검증하지 않는다" in result.stdout
    assert "Collab Gateway를 끕니다" in result.stdout
    assert "deploy-synctx.sh는 Jitsi를 끕니다" in result.stdout
    calls = (tmp_path / "calls.log").read_text()
    assert "-n synctx" in calls and "values-local.jitsi.secret.yaml" in calls
    tag = yaml.safe_load((ROOT / "helm/synctx/values.yaml").read_text())["jitsi"]["imageTag"]
    for component in ("web", "prosody", "jicofo", "jvb"):
        assert f"image jitsi/{component}:{tag}" in calls
