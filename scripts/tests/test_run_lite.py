"""Shell entrypoints preserve arguments and the caller's directory."""
import json
import os
from pathlib import Path
import subprocess

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
