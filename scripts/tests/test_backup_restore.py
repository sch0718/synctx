"""scripts/backup-restore.sh 시험. kubectl을 가짜로 바꿔 PVC 이름과 tar 전달을 확인한다."""

import os
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup-restore.sh"
TEMPLATES = ROOT / "helm" / "synctx" / "templates"

# get pvc: FAKE_PVC 하나만 있다고 답한다. exec: 백업이면 FAKE_DATA를 tar로 내보내고, 복구면 FAKE_RESTORE에 푼다.
FAKE_KUBECTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1" in
    get) [ "$2" = namespace ] && [ -z "$FAKE_NO_CLUSTER" ] || [ "$3" = "$FAKE_PVC" ] ;;
    run) echo "pod/$2 created" ;;
    exec)
        [ -n "$FAKE_EXEC_FAIL" ] && exit 1
        case " $* " in
            *" -i "*) tar xzf - -C "$FAKE_RESTORE" ;;
            *) tar czf - -C "$FAKE_DATA" . ;;
        esac ;;
esac
"""


def _env(tmp_path, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kubectl").write_text(FAKE_KUBECTL)
    (bin_dir / "kubectl").chmod(0o755)
    data = tmp_path / "pvc-data"
    data.mkdir()
    (data / "page.html").write_text("hello")
    restore = tmp_path / "restored"
    restore.mkdir()
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        FAKE_LOG=str(tmp_path / "calls.log"),
        FAKE_PVC="synctx-cdn-data",
        FAKE_DATA=str(data),
        FAKE_RESTORE=str(restore),
    )
    env.update(extra)
    return env


def _run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=20)


def test_pvc_names_match_helm_templates():
    script_names = set(re.findall(r'^\s+"([a-z-]+):/', SCRIPT.read_text(), re.M))
    helm_names = set()
    for tpl in TEMPLATES.glob("*-pvc.yaml"):
        helm_names.update(re.findall(r'include "synctx\.fullname" \. \}\}-([a-z-]+)', tpl.read_text()))
    assert script_names == helm_names


def test_backup_and_restore_stream_tar_through_exec(tmp_path):
    env = _env(tmp_path)
    backup_dir = tmp_path / "backup"

    result = _run(env, "backup", str(backup_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    archive = backup_dir / "cdn-data" / "data.tar.gz"
    with tarfile.open(archive) as tar:
        assert "./page.html" in tar.getnames()

    result = _run(env, "restore", str(backup_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "restored" / "page.html").read_text() == "hello"
    assert "delete pod synctx-restore-cdn-data" in (tmp_path / "calls.log").read_text()


def test_backup_fails_when_exec_fails(tmp_path):
    env = _env(tmp_path, FAKE_EXEC_FAIL="1")
    result = _run(env, "backup", str(tmp_path / "backup"))
    assert result.returncode == 1
    assert "[FAIL] synctx-cdn-data" in result.stdout
    assert "Backup complete" not in result.stdout


def test_backup_fails_when_nothing_found(tmp_path):
    env = _env(tmp_path, FAKE_PVC="none")
    result = _run(env, "backup", str(tmp_path / "backup"))
    assert result.returncode == 1
    assert "Nothing was backed up" in result.stdout


def test_restore_stops_when_cluster_unreachable(tmp_path):
    env = _env(tmp_path, FAKE_PVC="none", FAKE_NO_CLUSTER="1")
    (tmp_path / "backup" / "cdn-data").mkdir(parents=True)
    (tmp_path / "backup" / "cdn-data" / "data.tar.gz").write_bytes(b"")
    result = _run(env, "restore", str(tmp_path / "backup"))
    assert result.returncode == 1
    assert "닿지 못합니다" in result.stdout
