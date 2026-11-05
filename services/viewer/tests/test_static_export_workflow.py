import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "static-export.yml"
RAW = WORKFLOW.read_text(encoding="utf-8")
WF = yaml.safe_load(RAW)
TRIGGERS = WF.get("on", WF.get(True))


def _steps(job: str) -> list[dict]:
    return WF["jobs"][job]["steps"]


def _all_run_text(job: str) -> str:
    return "\n".join(s.get("run", "") for s in _steps(job))


def test_triggers_exclude_pull_request_family():
    assert "workflow_dispatch" in TRIGGERS
    for forbidden in ("pull_request", "pull_request_target", "push", "workflow_run", "issue_comment"):
        assert forbidden not in TRIGGERS


def test_dispatch_inputs_include_repos():
    inputs = TRIGGERS["workflow_dispatch"]["inputs"]
    assert "repos" in inputs
    assert inputs["repos"].get("required") is not True


def test_permissions_are_minimal():
    assert WF["permissions"] == {"contents": "read"}
    for job in WF["jobs"].values():
        assert set(job.get("permissions", {})) <= {"contents"}


def test_concurrency_does_not_cancel_running_deploy():
    assert WF["concurrency"]["cancel-in-progress"] is False


def test_deploy_needs_build():
    needs = WF["jobs"]["deploy"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert "build" in needs


def test_deploy_ref_is_fixed_production_branch():
    deploy = "\n".join(
        str(s.get("with", {}).get("command", "")) for s in _steps("deploy")
    )
    assert "pages deploy dist" in deploy
    branches = re.findall(r"--branch=(\S+)", deploy)
    assert branches and all(re.fullmatch(r"[A-Za-z0-9._/-]+", b) for b in branches)
    assert "${{" not in " ".join(branches)


def test_every_pages_deploy_uses_fixed_branch():
    for match in re.finditer(r"pages deploy[^\n]*", RAW):
        assert re.search(r"--branch=[A-Za-z0-9._/-]+(\s|$|\")", match.group(0)), match.group(0)


def test_secrets_only_via_secrets_context():
    assert not re.search(r"(?i)(ghp_|github_pat_)", RAW)
    for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "GIT_READ_TOKEN"):
        assert f"secrets.{name}" in RAW
    for line in RAW.splitlines():
        if re.match(r"\s*(CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID|GIT_READ_TOKEN)\s*:", line):
            assert "${{ secrets." in line, line


def test_secrets_not_interpolated_into_run_scripts():
    for job in WF["jobs"]:
        assert "secrets." not in _all_run_text(job)
        assert "inputs." not in _all_run_text(job)


def test_secrets_not_set_at_workflow_level():
    assert "env" not in WF or not any("secrets." in str(v) for v in WF["env"].values())


def test_git_read_token_only_in_build_job():
    for job in ("deploy",):
        assert "GIT_READ_TOKEN" not in yaml.safe_dump(WF["jobs"][job])


def test_actions_use_pinned_version_tags():
    for uses in re.findall(r"uses:\s*(\S+)", RAW):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+", uses), uses


def test_build_job_runs_build_script_and_uploads_dist():
    text = _all_run_text("build")
    assert "scripts/static-export/build.sh" in text
    uses = [s.get("uses", "") for s in _steps("build")]
    assert any(u.startswith("actions/upload-artifact@") for u in uses)


def test_deploy_runs_check_access_as_required_step():
    steps = _steps("deploy")
    check = [s for s in steps if "check-access.sh" in s.get("run", "")]
    assert check
    for step in check:
        assert not step.get("continue-on-error")
    deploy_index = next(i for i, s in enumerate(steps) if "wrangler-action" in s.get("uses", ""))
    check_index = steps.index(check[-1])
    assert check_index > deploy_index


def test_preflight_deploys_placeholder_without_documents():
    pre = WF["jobs"]["preflight"]
    text = yaml.safe_dump(pre)
    assert "check-access.sh" in text
    assert "GIT_READ_TOKEN" not in text
    assert "scripts/static-export/build.sh" not in text
    assert "pages deploy dist" not in text


def test_deploy_waits_for_preflight_when_requested():
    deploy = WF["jobs"]["deploy"]
    assert "preflight" in deploy["needs"]
    assert "needs.build.result == 'success'" in deploy["if"]
    assert "needs.preflight.result" in deploy["if"]


def test_unverified_cloudflare_behaviors_are_marked():
    assert "미확인" in RAW
