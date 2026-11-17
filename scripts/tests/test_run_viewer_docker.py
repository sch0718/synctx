"""Single-container launcher behavior, without contacting a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/docker/run-viewer.sh"
REPO_URL = "https://git.example/team/docs with spaces.git"


@pytest.fixture
def docker(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "docker"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if sys.argv[1:3] == ['image', 'inspect']:\n"
        "    with open(os.environ['INSPECT_LOG'], 'a') as log:\n"
        "        log.write(json.dumps(sys.argv[3:]) + '\\n')\n"
        "    sys.exit(int(os.environ.get('DOCKER_INSPECT_EXIT', '0')))\n"
        "with open(os.environ['CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "        'public_url': os.environ.get('SYNCTX_VIEWER_PUBLIC_BASE_URL')}) + '\\n')\n"
        "sys.exit(int(os.environ.get('DOCKER_EXIT', '0')))\n"
    )
    executable.chmod(0o755)
    # Do not inherit developer tokens or Docker settings in the fake process.
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "CALL_LOG": str(tmp_path / "calls.jsonl"), "INSPECT_LOG": str(tmp_path / "inspect.jsonl")}
    return env


def run(docker, tmp_path, *args, launcher=LAUNCHER):
    result = subprocess.run(["bash", str(launcher), *args], cwd=tmp_path,
                            env=docker, capture_output=True, text=True)
    log = Path(docker["CALL_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def values(args, flag):
    return [args[i + 1] for i, value in enumerate(args) if value == flag]


def app_args(call):
    args = call["args"]
    return args[args.index("python") + 2:]


def test_one_container_uses_loopback_named_cache_and_standalone(docker, tmp_path):
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    call = calls[0]
    args = call["args"]
    assert args[:2] == ["run", "--rm"]
    assert values(args, "--publish") == ["127.0.0.1:18000:8000"]
    assert values(args, "--volume") == ["synctx-viewer-cache:/data/viewer"]
    assert values(args, "--name") == ["synctx-viewer"]
    assert values(args, "--env") == ["GIT_READ_TOKEN", "SYNCTX_SERVICE_TOKEN", "SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS", "SYNCTX_VIEWER_PUBLIC_BASE_URL", "SYNCTX_ENABLED_SERVICES=viewer"]
    sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    assert f"synctx/viewer:0.8.3-{sha}" in args
    assert args[args.index("python") + 1] == "/app/services/viewer/src/standalone.py"
    assert app_args(call) == ["--repo", REPO_URL, "--host", "0.0.0.0", "--port", "8000", "--cache-dir", "/data/viewer"]
    assert call["public_url"] == "http://127.0.0.1:18000"
    assert call["cwd"] == str(tmp_path)


def test_docker_and_shared_arguments_keep_boundaries(docker, tmp_path):
    result, calls = run(docker, tmp_path, "--image", "registry.example:5000/team/viewer:0.6.0-abcdef0",
                        "--repo", REPO_URL, "--repo", "git@git.example:team/other.git", "--ref", "feature/docs",
                        "--port", "18123", "--bind", "0.0.0.0", "--name", "docs-viewer", "--volume", "docs-cache",
                        "--refresh-interval", "12.5", "--public-base-url", "https://docs.example/prefix")
    assert result.returncode == 0, result.stderr
    args = calls[0]["args"]
    assert values(args, "--publish") == ["0.0.0.0:18123:8000"]
    assert values(args, "--volume") == ["docs-cache:/data/viewer"]
    assert values(args, "--name") == ["docs-viewer"]
    assert "registry.example:5000/team/viewer:0.6.0-abcdef0" in args
    assert app_args(calls[0]) == ["--repo", REPO_URL, "--repo", "git@git.example:team/other.git",
                                "--ref", "feature/docs", "--refresh-interval", "12.5",
                                "--public-base-url", "https://docs.example/prefix", "--host", "0.0.0.0",
                                "--port", "8000", "--cache-dir", "/data/viewer"]
    assert calls[0]["public_url"] == "http://127.0.0.1:18123"


def test_config_mount_is_readonly_caller_relative_and_unchanged(docker, tmp_path):
    folder = tmp_path / "my configs"
    folder.mkdir()
    config = folder / "viewer.yaml"
    content = "authz:\n  enabled: true\nrepositories: []\n"
    config.write_text(content)
    result, calls = run(docker, tmp_path, "--config", "my configs/viewer.yaml")
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--volume") == ["synctx-viewer-cache:/data/viewer", f"{config}:/etc/synctx/standalone.yaml:ro"]
    assert app_args(calls[0])[:2] == ["--config", "/etc/synctx/standalone.yaml"]
    assert config.read_text() == content


def test_local_repository_is_canonicalized_and_only_that_directory_mounted(docker, tmp_path):
    repo = tmp_path / "my repo"
    repo.mkdir()
    (tmp_path / "alias").symlink_to(repo, target_is_directory=True)
    result, calls = run(docker, tmp_path, "--repo", "./alias")
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--volume") == ["synctx-viewer-cache:/data/viewer", f"{repo}:{repo}:ro"]
    assert app_args(calls[0])[:2] == ["--repo", str(repo)]


def test_tokens_pass_by_name_without_values(docker, tmp_path):
    docker.update(GIT_READ_TOKEN="fake-secret-a", SYNCTX_SERVICE_TOKEN="fake-secret-b", CUSTOM_GIT_TOKEN="fake-secret-c")
    result, calls = run(docker, tmp_path, "--repo", REPO_URL, "--env", "CUSTOM_GIT_TOKEN")
    assert result.returncode == 0, result.stderr
    assert "CUSTOM_GIT_TOKEN" in values(calls[0]["args"], "--env")
    assert "fake-secret" not in json.dumps(calls) + result.stdout + result.stderr


def test_render_keys_are_forwarded_by_name_without_logging(docker, tmp_path):
    docker.update(SYNCTX_RENDER_SECRET="current-never-print" * 2, SYNCTX_RENDER_SECRET_PREVIOUS="previous-never-print" * 2)
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    forwarded = values(calls[0]["args"], "--env")
    assert "SYNCTX_RENDER_SECRET" in forwarded
    assert "SYNCTX_RENDER_SECRET_PREVIOUS" in forwarded
    assert "never-print" not in json.dumps(calls) + result.stdout + result.stderr


def test_render_key_files_are_mounted_readonly_at_fixed_paths(docker, tmp_path):
    for name, filename in [("SYNCTX_RENDER_SECRET_FILE", "synctx-render-secret"),
                           ("SYNCTX_RENDER_SECRET_PREVIOUS_FILE", "synctx-render-secret-previous")]:
        host = tmp_path / (filename + " with spaces")
        host.write_text("file-secret-never-print" * 2)
        docker[name] = host.name
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    args = calls[0]["args"]
    for name, filename in [("SYNCTX_RENDER_SECRET_FILE", "synctx-render-secret"),
                           ("SYNCTX_RENDER_SECRET_PREVIOUS_FILE", "synctx-render-secret-previous")]:
        assert f"{tmp_path / docker[name]}:/run/secrets/{filename}:ro" in values(args, "--volume")
        assert f"{name}=/run/secrets/{filename}" in values(args, "--env")
        assert name not in values(args, "--env")
    assert "file-secret-never-print" not in json.dumps(calls) + result.stdout + result.stderr


@pytest.mark.parametrize("name", ["SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS"])
@pytest.mark.parametrize("scenario", ["both", "missing", "colon", "directory"])
def test_invalid_render_key_files_fail_before_docker(docker, tmp_path, name, scenario):
    host = tmp_path / ("key:bad" if scenario == "colon" else "key")
    if scenario == "directory":
        host.mkdir()
    elif scenario != "missing":
        host.write_text("file-secret-never-print" * 2)
    docker[name + "_FILE"] = str(host)
    if scenario == "both":
        docker[name] = "direct-secret-never-print" * 2
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode != 0
    assert name + "_FILE" in result.stderr
    assert "secret-never-print" not in result.stdout + result.stderr
    assert not calls


@pytest.mark.parametrize("name", ["SYNCTX_RENDER_SECRET_FILE", "SYNCTX_RENDER_SECRET_PREVIOUS_FILE",
                                 "SYNCTX_RENDER_SECRET_FILE_MOUNT", "SYNCTX_RENDER_SECRET_PREVIOUS_FILE_MOUNT"])
def test_render_file_environment_cannot_override_launcher(docker, tmp_path, name):
    result, calls = run(docker, tmp_path, "--repo", REPO_URL, "--env", name)
    assert result.returncode != 0
    assert "launcher-managed" in result.stderr
    assert not calls


def test_public_url_environment_preserved_for_app_fallback(docker, tmp_path):
    docker["SYNCTX_VIEWER_PUBLIC_BASE_URL"] = "https://docs.example"
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    assert calls[0]["public_url"] == "https://docs.example"


@pytest.mark.parametrize("bind,publication,url", [
    ("::1", "[::1]:18000:8000", "http://[::1]:18000"),
    ("::", "[::]:18000:8000", "http://[::1]:18000"),
    ("192.0.2.1", "192.0.2.1:18000:8000", "http://192.0.2.1:18000"),
])
def test_bind_address_publication(docker, tmp_path, bind, publication, url):
    result, calls = run(docker, tmp_path, "--repo", REPO_URL, "--bind", bind)
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--publish") == [publication]
    assert calls[0]["public_url"] == url


@pytest.mark.parametrize("args,message", [
    ([], "--repo"),
    (["--repo"], "--repo"),
    (["--repo", REPO_URL, "--config", "x.yaml"], "--config"),
    (["--config", "missing.yaml"], "config"),
    (["--repo", "missing-repo"], "repo"),
    (["--repo", REPO_URL, "--bogus"], "--bogus"),
    (["--repo", REPO_URL, "--port"], "--port"),
    (["--repo", REPO_URL, "--port", "0"], "port"),
    (["--repo", REPO_URL, "--port", "65536"], "port"),
    (["--repo", REPO_URL, "--port", "2;echo leak"], "port"),
    (["--repo", REPO_URL, "--bind", "127.0.0.1:80"], "bind"),
    (["--repo", REPO_URL, "--bind", "999.1.1.1"], "bind"),
    (["--repo", REPO_URL, "--bind", "::1:"], "bind"),
    (["--repo", REPO_URL, "--ref"], "--ref"),
    (["--repo", REPO_URL, "--refresh-interval"], "--refresh-interval"),
    (["--repo", REPO_URL, "--public-base-url"], "--public-base-url"),
    (["--repo", REPO_URL, "--name", "bad name"], "name"),
    (["--repo", REPO_URL, "--volume", "/host/path"], "volume"),
    (["--repo", REPO_URL, "--env", "TOKEN=secret"], "env"),
    (["--repo", REPO_URL, "--env", "SYNCTX_ENABLED_SERVICES"], "env"),
    (["--repo", REPO_URL, "--env", "GIT_CONFIG_COUNT"], "launcher-managed"),
    (["--repo", REPO_URL, "--env", "GIT_CONFIG_KEY_0"], "launcher-managed"),
    (["--repo", REPO_URL, "--env", "GIT_CONFIG_VALUE_0"], "launcher-managed"),
    (["--repo", REPO_URL, "--host", "0.0.0.0"], "--bind"),
    (["--repo", REPO_URL, "--cache-dir", "/host"], "--volume"),
    (["--repo", REPO_URL, "--image", "viewer"], "image"),
    (["--repo", REPO_URL, "--image", "viewer:latest"], "image"),
    (["--repo", REPO_URL, "--image", "viewer:0.6.0"], "image"),
    (["--repo", REPO_URL, "--image", "viewer:0.6.0-notsha"], "image"),
    (["--repo", REPO_URL, "--image", "viewer:0.6.0-abcdef0;id"], "image"),
    (["--repo", REPO_URL, "--build", "--image", "viewer:0.6.0-abcdef0"], "--build"),
])
def test_invalid_arguments_never_invoke_docker(docker, tmp_path, args, message):
    result, calls = run(docker, tmp_path, *args)
    assert result.returncode != 0
    assert message in result.stderr
    assert calls == []


def test_help_works_without_docker(docker, tmp_path):
    result, calls = run(docker, tmp_path, "--help")
    assert result.returncode == 0
    for flag in ("--repo", "--config", "--image", "--build", "--bind", "--port", "--volume", "--env"):
        assert flag in result.stdout
    assert calls == []


def test_build_delegates_only_viewer_no_load_and_matching_registry(docker, tmp_path):
    wrapper = tmp_path / "checkout/scripts/docker/run-viewer.sh"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, wrapper)
    build = wrapper.parent.parent / "local/steps/build.sh"
    build.parent.mkdir(parents=True)
    build.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                     "with open(os.environ['CALL_LOG'], 'a') as log:\n"
                     "    log.write(json.dumps({'build': sys.argv[1:], 'registry': os.environ['DOCKER_REGISTRY']}) + '\\n')\n"
                     "sys.exit(int(os.environ.get('BUILD_EXIT', '0')))\n")
    build.chmod(0o755)
    # A checkout is needed only to derive the immutable default tag, never to run --image.
    subprocess.run(["git", "init", "-q", str(tmp_path / "checkout")], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
                   cwd=tmp_path / "checkout", check=True)
    sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=tmp_path / "checkout", text=True).strip()
    docker["DOCKER_REGISTRY"] = "registry.example:5000/team"
    result, calls = run(docker, tmp_path, "--build", "--repo", REPO_URL, launcher=wrapper)
    assert result.returncode == 0, result.stderr
    assert calls[0] == {"build": ["--only", "viewer", "--no-load", "--tag", f"0.8.3-{sha}"], "registry": "registry.example:5000/team"}
    assert f"registry.example:5000/team/viewer:0.8.3-{sha}" in calls[1]["args"]
    assert len(calls) == 2
    docker["BUILD_EXIT"] = "7"
    Path(docker["CALL_LOG"]).unlink()
    result, calls = run(docker, tmp_path, "--build", "--repo", REPO_URL, launcher=wrapper)
    assert result.returncode == 7
    assert len(calls) == 1 and "build" in calls[0]


def test_prebuilt_image_runs_without_git_checkout(docker, tmp_path):
    wrapper = tmp_path / "bundle/scripts/docker/run-viewer.sh"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, wrapper)
    result, calls = run(docker, tmp_path, "--image", "synctx/viewer:0.6.0-abcdef0", "--repo", REPO_URL, launcher=wrapper)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1


def test_docker_failure_status_propagates(docker, tmp_path):
    docker["DOCKER_EXIT"] = "125"
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 125
    assert len(calls) == 1


def test_repeated_local_repositories_share_one_mount(docker, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "alias").symlink_to(repo, target_is_directory=True)
    result, calls = run(docker, tmp_path, "--repo", "repo", "--repo", "alias")
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--volume").count(f"{repo}:{repo}:ro") == 1
    assert app_args(calls[0])[:4] == ["--repo", str(repo), "--repo", str(repo)]


@pytest.mark.parametrize("source_kind", ["plain", "escaped", "localhost"])
def test_local_file_url_mounts_selected_directory(docker, tmp_path, source_kind):
    repo = tmp_path / ("docs with spaces + literal%" if source_kind == "escaped" else "docs")
    repo.mkdir()
    source = repo.as_uri()
    if source_kind == "localhost":
        source = source.replace("file:///", "file://localhost/", 1)
    result, calls = run(docker, tmp_path, "--repo", source)
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--volume") == ["synctx-viewer-cache:/data/viewer", f"{repo}:{repo}:ro"]
    assert app_args(calls[0])[:2] == ["--repo", str(repo)]


@pytest.mark.parametrize("source,message", [
    ("file:///not-present/synctx-repository", "local repo directory does not exist"),
    ("file://remote.example/path/to/repo", "file URL host"),
    ("file:///tmp/repo%XY", "file URL"),
    ("file:///tmp/repo%00", "file URL"),
    ("file:///tmp/repo?ref=main", "file URL"),
    ("file:///tmp/repo#fragment", "file URL"),
])
def test_invalid_local_file_url_fails_before_docker(docker, tmp_path, source, message):
    result, calls = run(docker, tmp_path, "--repo", source)
    assert result.returncode != 0
    assert message in result.stderr
    assert calls == []


@pytest.mark.parametrize("source_kind", ["path", "file_url"])
def test_linked_git_worktree_or_submodule_rejected_before_docker(docker, tmp_path, source_kind):
    repo = tmp_path / "linked repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /outside/admin/worktrees/linked\n")
    source = str(repo) if source_kind == "path" else repo.as_uri()
    result, calls = run(docker, tmp_path, "--repo", source)
    assert result.returncode != 0
    assert "linked worktree or submodule" in result.stderr
    assert "remote Git URL" in result.stderr
    assert calls == []


def test_local_repositories_are_trusted_by_git_in_container(docker, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    result, calls = run(docker, tmp_path, "--repo", "first", "--repo", "second", "--repo", "first")
    assert result.returncode == 0, result.stderr
    envs = values(calls[0]["args"], "--env")
    assert "GIT_CONFIG_COUNT=2" in envs
    assert "GIT_CONFIG_KEY_0=safe.directory" in envs and f"GIT_CONFIG_VALUE_0={first}" in envs
    assert "GIT_CONFIG_KEY_1=safe.directory" in envs and f"GIT_CONFIG_VALUE_1={second}" in envs
    remote_envs = values(run(docker, tmp_path, "--repo", REPO_URL)[1][-1]["args"], "--env")
    assert not any(e.startswith("GIT_CONFIG") for e in remote_envs)


def inspected(docker):
    log = Path(docker["INSPECT_LOG"])
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("order", ["ref_first", "config_first"])
def test_ref_with_config_is_rejected_before_docker(docker, tmp_path, order):
    (tmp_path / "viewer.yaml").write_text("repositories: []\n")
    args = ["--ref", "release", "--config", "viewer.yaml"]
    if order == "config_first":
        args = args[2:] + args[:2]
    result, calls = run(docker, tmp_path, *args)
    assert result.returncode != 0
    assert "--ref" in result.stderr and "--config" in result.stderr
    assert calls == [] and inspected(docker) == []


def test_default_image_without_registry_never_pulls(docker, tmp_path):
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    image = next(arg for arg in calls[0]["args"] if arg.startswith("synctx/viewer:"))
    assert values(calls[0]["args"], "--pull") == ["never"]
    assert inspected(docker) == [[image]]


def test_missing_default_image_explains_build_before_docker_run(docker, tmp_path):
    docker["DOCKER_INSPECT_EXIT"] = "1"
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode != 0
    assert "--build" in result.stderr and "--image" in result.stderr
    assert calls == []


def test_registry_explicit_image_and_build_keep_docker_pull_policy(docker, tmp_path):
    docker["DOCKER_REGISTRY"] = "registry.local:5000/synctx"
    result, calls = run(docker, tmp_path, "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    assert values(calls[0]["args"], "--pull") == []
    del docker["DOCKER_REGISTRY"]
    result, calls = run(docker, tmp_path, "--image", "synctx/viewer:0.6.0-abcdef0", "--repo", REPO_URL)
    assert result.returncode == 0, result.stderr
    assert values(calls[-1]["args"], "--pull") == []
    assert inspected(docker) == []
