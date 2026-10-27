"""Shell entrypoints preserve arguments and the caller's directory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_local_launcher_selects_only_viewer_and_keeps_cwd(tmp_path):
    binary = tmp_path / "uv"
    binary.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd()}))\n')
    binary.chmod(0o755)
    result = subprocess.run([str(ROOT / "run.sh"), "--repo", "https://git.example/a b.git", "--port", "18001"],
                            cwd=tmp_path, env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
                            check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    assert data["cwd"] == str(tmp_path)
    assert data["args"] == ["run", "--frozen", "--project", str(ROOT), "--package", "synctx-viewer", "--no-dev",
                            "python", str(ROOT / "services/viewer/src/standalone.py"),
                            "--repo", "https://git.example/a b.git", "--port", "18001"]


def test_root_dispatches_docker_only_as_first_argument(tmp_path):
    # Isolate the dispatch contract from Docker, added by Task 2.
    (tmp_path / "run.sh").write_bytes((ROOT / "run.sh").read_bytes())
    (tmp_path / "run.sh").chmod(0o755)
    for mode, target in (("docker", "run-viewer.sh"), ("local", "run-lite.sh")):
        folder = tmp_path / "scripts" / mode
        folder.mkdir(parents=True)
        (folder / target).write_text(f'#!/bin/bash\nprintf "%s\\n" {mode} "$@"\n')
        (folder / target).chmod(0o755)
    result = subprocess.run([str(tmp_path / "run.sh"), "--docker", "--repo", "relative repo"], check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ["docker", "--repo", "relative repo"]
    result = subprocess.run([str(tmp_path / "run.sh"), "--repo", "--docker"], check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == ["local", "--repo", "--docker"]


@pytest.mark.parametrize("ttl", [None, 7200])
@pytest.mark.parametrize("embed", [None, {"enabled": True}, {"enabled": False, "render_files": True}])
def test_lite_delegates_render_config_without_key_serialization(tmp_path, ttl, embed):
    binary = tmp_path / "uv"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nfrom pathlib import Path\n"
        f"source = Path({str(ROOT / 'services/viewer/src/standalone.py')!r})\n"
        "sys.path.insert(0, str(source.parent))\nimport standalone\n"
        "args = standalone.parse_args(sys.argv[sys.argv.index(str(source)) + 1:])\n"
        "print(standalone.prepare_config(args).read_text())\n"
    )
    binary.chmod(0o755)
    source = {"repositories": [{"id": "docs", "name": "Docs", "url": "https://git.example/docs.git"}]}
    if ttl is not None:
        source["viewer"] = {"render_token_ttl_seconds": ttl}
    if embed is not None:
        source["repositories"][0]["html_embed"] = embed
    config = tmp_path / "input.yaml"
    config.write_text(yaml.safe_dump(source))
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "SYNCTX_RENDER_SECRET": "current-never-print" * 2,
           "SYNCTX_RENDER_SECRET_PREVIOUS": "previous-never-print" * 2}
    result = subprocess.run([str(ROOT / "scripts/local/run-lite.sh"), "--config", str(config),
                             "--cache-dir", str(tmp_path / "cache")],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    generated = yaml.safe_load(result.stdout)
    assert generated["viewer"]["render_token_ttl_seconds"] == (14400 if ttl is None else ttl)
    assert generated["repositories"][0].get("html_embed") == embed
    assert "never-print" not in result.stdout + result.stderr
