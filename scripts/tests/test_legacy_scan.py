"""scripts/legacy-scan.sh의 허용 목록 비교(네트워크 없이 --input으로 시험)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "legacy-scan.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq 필요")


def _scan(findings: list[tuple[str, str, int]]) -> dict:
    """(규칙 id, 파일, 줄) 목록으로 legacy-scan --json 모양을 만든다."""
    guides: dict[str, dict] = {}
    for rule, file, line in findings:
        guide = guides.setdefault(rule, {"guideId": rule.split("/")[0], "rules": [{"ruleId": rule, "findings": []}]})
        guide["rules"][0]["findings"].append({"file": file, "line": line, "message": "옛 패턴"})
    return {"targetDir": "x", "totalFindings": len(findings), "guides": list(guides.values())}


def _run(tmp_path: Path, findings, allow: str) -> subprocess.CompletedProcess:
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps(_scan(findings)), encoding="utf-8")
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text(allow, encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), "--input", str(scan)],
        capture_output=True, text=True, env={**os.environ, "LEGACY_SCAN_ALLOW": str(allow_file)},
    )


def test_passes_when_all_findings_are_allowed(tmp_path):
    result = _run(tmp_path, [("scrollspy/js", "viewer.js", 549)], "# 이유\nscrollspy/js viewer.js 1  # 대체 코드\n")
    assert result.returncode == 0, result.stderr
    assert "통과" in result.stdout


def test_fails_on_new_rule_or_extra_count(tmp_path):
    findings = [("scrollspy/js", "viewer.js", 549), ("scrollspy/js", "viewer.js", 700), ("js/copying-array-methods", "a.js", 3)]
    result = _run(tmp_path, findings, "scrollspy/js viewer.js 1\n")
    assert result.returncode == 1
    assert "scrollspy/js viewer.js 2건 (허용 1건)" in result.stderr
    assert "js/copying-array-methods a.js 1건 (허용 0건)" in result.stderr
    assert "a.js:3" in result.stderr


def test_repository_allow_list_matches_known_format():
    for line in (ROOT / "scripts" / "legacy-scan-allow.txt").read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if body:
            rule, file, count = body.split()
            assert int(count) >= 1 and rule and file


@pytest.mark.parametrize("allow", ["r a.js 1\nr a.js 1\n", "r a.js one\n", "r a.js\n"])
def test_malformed_allow_list_stops_instead_of_passing(tmp_path, allow):
    result = _run(tmp_path, [("r", "a.js", 1), ("r", "a.js", 2)], allow)
    assert result.returncode == 2
    assert "허용 목록 형식 오류" in result.stderr


def test_crlf_allow_list_is_read(tmp_path):
    result = _run(tmp_path, [("r", "a.js", 1)], "r a.js 1\r\n")
    assert result.returncode == 0, result.stderr
