"""설치 구성 프리셋(Helm values·Compose env)과 docs/install.md 검사. helm이 없으면 Helm 시험은 건너뛴다."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm" / "synctx"
HELM_PROFILES = CHART
COMPOSE_PROFILES = ROOT / "deploy" / "docker" / "profiles"
ENV_EXAMPLE = ROOT / "deploy" / "docker" / "synctx.env.example"
DEPLOY = ROOT / "scripts" / "docker" / "deploy.sh"
INSTALL_DOC = ROOT / "docs" / "install.md"

COMPOSE_SERVICES = {"viewer", "cdn", "marp", "slides", "drawio"}
COMPOSE_PROFILE_SERVICES = {
    "docs": {"viewer", "cdn"},
    "present": {"viewer", "cdn", "marp", "slides"},
    "all-supported": COMPOSE_SERVICES,
}
HELM_PROFILE_WORKLOADS = {
    "docs": {"synctx-viewer", "synctx-cdn"},
    "present": {"synctx-viewer", "synctx-cdn", "synctx-marp", "synctx-slides"},
    "full": {
        "synctx-viewer", "synctx-cdn", "synctx-marp", "synctx-slides", "synctx-drawio",
        "synctx-sync", "synctx-feedback", "synctx-pushgateway",
    },
}
UNCHANGED_DEFAULTS = ["helm/synctx/values.yaml", "deploy/docker/synctx.env.example"]

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm 미설치")


def _parse_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _workloads(profile, *base_values):
    files = [*base_values, HELM_PROFILES / f"values-profile-{profile}.yaml"]
    result = subprocess.run(
        ["helm", "template", "synctx", str(CHART), *[a for f in files for a in ("-f", str(f))]],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    return {d["metadata"]["name"] for d in docs if d["kind"] in ("Deployment", "CronJob")}


@needs_helm
@pytest.mark.parametrize("profile", sorted(HELM_PROFILE_WORKLOADS))
def test_helm_profile_enables_expected_workloads(profile):
    assert _workloads(profile) == HELM_PROFILE_WORKLOADS[profile]


@needs_helm
@pytest.mark.parametrize("profile", sorted(HELM_PROFILE_WORKLOADS))
def test_helm_profile_after_production_values_keeps_service_set(profile):
    assert _workloads(profile, CHART / "values-production.yaml") == HELM_PROFILE_WORKLOADS[profile]


@pytest.mark.parametrize("profile", sorted(HELM_PROFILE_WORKLOADS))
def test_helm_profile_does_not_override_image_tags_or_jitsi(profile):
    values = yaml.safe_load((HELM_PROFILES / f"values-profile-{profile}.yaml").read_text(encoding="utf-8"))
    assert not (values.get("jitsi") or {}).get("enabled", False)
    assert not (values.get("collabGateway") or {}).get("enabled", False)
    for service, body in values.items():
        assert "image" not in (body or {}), f"{service}: 프리셋은 이미지 태그를 바꾸지 않는다"


@pytest.mark.parametrize("profile", sorted(COMPOSE_PROFILE_SERVICES))
def test_compose_profile_services_are_supported(profile):
    services = set(_parse_env(COMPOSE_PROFILES / f"{profile}.env")["SYNCTX_SERVICES"].split(","))
    assert services == COMPOSE_PROFILE_SERVICES[profile]
    assert services <= COMPOSE_SERVICES


def test_compose_profile_is_not_named_full():
    assert not (COMPOSE_PROFILES / "full.env").exists()


@pytest.mark.parametrize("profile", sorted(COMPOSE_PROFILE_SERVICES))
def test_compose_profile_differs_from_example_only_in_services(profile):
    example = _parse_env(ENV_EXAMPLE)
    values = _parse_env(COMPOSE_PROFILES / f"{profile}.env")
    assert set(values) == set(example)
    assert {k for k in values if values[k] != example[k]} <= {"SYNCTX_SERVICES"}
    assert values["SYNCTX_JITSI"] == "false"
    assert values["SYNCTX_IMAGE_TAG"] != "latest" and "latest" not in values["SYNCTX_IMAGE_TAG"]


@pytest.mark.parametrize("profile", sorted(COMPOSE_PROFILE_SERVICES))
def test_compose_profile_passes_deploy_check(profile, tmp_path):
    config = tmp_path / "synctx.yaml"
    config.write_text("authz:\n  enabled: false\n")
    values = _parse_env(COMPOSE_PROFILES / f"{profile}.env")
    values["SYNCTX_CONFIG_FILE"] = str(config)
    env_file = tmp_path / "synctx.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNCTX_")}
    env["SYNCTX_GENERATED_DIR"] = str(tmp_path / "generated")
    result = subprocess.run(
        ["bash", str(DEPLOY), "--env-file", str(env_file), "--secrets-file", str(tmp_path / "none.env"), "--check"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _base_ref():
    for ref in ("origin/main", "main"):
        found = subprocess.run(["git", "merge-base", "HEAD", ref], cwd=ROOT, capture_output=True, text=True)
        if found.returncode == 0:
            return found.stdout.strip()
    return None


def test_default_values_and_env_example_are_unchanged():
    base = _base_ref()
    if base is None:
        pytest.skip("main 기준 커밋을 찾을 수 없음")
    for args in (["HEAD"], [base, "HEAD"]):
        result = subprocess.run(
            ["git", "diff", "--quiet", *args, "--", *UNCHANGED_DEFAULTS], cwd=ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, f"기본 값 파일이 바뀌었다: {args}"
    worktree = subprocess.run(
        ["git", "diff", "--quiet", "--", *UNCHANGED_DEFAULTS], cwd=ROOT, capture_output=True, text=True
    )
    assert worktree.returncode == 0


def _doc():
    return INSTALL_DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "needle",
    [
        "static-export.yaml",
        "scripts/static-export/build.sh",
        "scripts/static-export/check-access.sh",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "GIT_READ_TOKEN",
        "SYNCTX_JITSI",
        "SYNCTX_JITSI_ADVERTISE_IPS",
        "values-profile-full.yaml",
        "profiles/all-supported.env",
        "미확인",
        "Helm만",
    ],
)
def test_install_doc_mentions(needle):
    assert needle in _doc()


def test_install_doc_orders_first_deploy_steps():
    text = _doc()
    section = text[text.index("### 첫 배포 순서"):text.index("### 한도")]
    steps = re.findall(r"^\d\. \*\*(.+?)\*\*", section, flags=re.M)
    assert steps == ["Access 앱", "사전 점검", "배포", "배포 후 점검"]
    assert "통과" in section and "공유" in section


def test_install_doc_splits_rollback_and_orders_site_before_access():
    text = _doc()
    rollback = text[text.index("## 롤백"):]
    assert "정적 기능" in rollback and "corporate" in rollback
    assert rollback.index("사이트") < rollback.index("Access")


def test_install_doc_rules():
    text = _doc()
    assert "%%{init" not in text and "classDef" not in text
    assert not re.search(r"(?i)(tag:\s*\"?latest|TAG=latest|:latest\b)", text), "latest 태그 사용 예시 금지"
    assert "team_docs" not in text
