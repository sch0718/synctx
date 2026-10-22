"""Helm이 렌더한 PrometheusRule을 promtool로 평가한다. helm이나 promtool이 없으면 건너뛴다.

promtool 대신 컨테이너를 쓰려면 PROMTOOL에 명령을 준다.
예: PROMTOOL="docker run --rm -v {dir}:{dir} -w {dir} --entrypoint promtool prom/prometheus:v2.39.1"
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm" / "synctx"
RULE_TESTS = Path(__file__).resolve().parent / "prometheus"


def _promtool(workdir):
    command = os.environ.get("PROMTOOL")
    if command:
        return shlex.split(command.format(dir=workdir))
    return ["promtool"] if shutil.which("promtool") else None


pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None or (not os.environ.get("PROMTOOL") and shutil.which("promtool") is None),
    reason="helm 또는 promtool 미설치",
)


def _rules():
    result = subprocess.run(
        ["helm", "template", "synctx", str(CHART), "-s", "templates/prometheusrule.yaml"],
        capture_output=True, text=True, check=True,
    )
    rule = yaml.safe_load(result.stdout)
    return {"groups": rule["spec"]["groups"]}


@pytest.mark.parametrize("spec", sorted(p.name for p in RULE_TESTS.glob("*.test.yaml")))
def test_alert_rules(tmp_path, spec):
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(_rules(), allow_unicode=True), encoding="utf-8")
    shutil.copy(RULE_TESTS / spec, tmp_path / spec)
    result = subprocess.run(
        [*_promtool(tmp_path), "test", "rules", spec], cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
