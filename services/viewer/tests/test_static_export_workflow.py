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
    for name, job in WF["jobs"].items():
        allowed = {"contents": "read", "packages": "read"} if name == "deploy" else {"contents": "read"}
        assert job.get("permissions", {}).items() <= allowed.items(), name


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


PUBLIC_OFF = "vars.STATIC_EXPORT_PUBLIC != 'true'"
LOGIN_ON = "vars.STATIC_EXPORT_LOGIN == 'true'"
LOGIN_OFF = "vars.STATIC_EXPORT_LOGIN != 'true'"


def _deploy_steps_using(fragment: str) -> list[dict]:
    return [s for s in _steps("deploy") if fragment in yaml.safe_dump(s)]


def test_access_check_is_skipped_only_for_explicit_public_or_login_mode():
    # 변수가 없거나 'true'가 아니면 Access 점검이 돈다. 공개·로그인 모드는 명시적으로만 켠다.
    check = [s for s in _steps("deploy") if "check-access.sh" in s.get("run", "") and "--mode login" not in s["run"]]
    assert [s.get("if") for s in check] == ["${{ " + PUBLIC_OFF + " && " + LOGIN_OFF + " }}"]
    assert PUBLIC_OFF in WF["jobs"]["preflight"]["if"]


def test_preflight_is_access_mode_only():
    assert LOGIN_OFF in WF["jobs"]["preflight"]["if"]


def test_build_fails_when_public_and_login_are_both_on():
    first = _steps("build")[0]
    assert first["if"] == "${{ vars.STATIC_EXPORT_PUBLIC == 'true' && " + LOGIN_ON + " }}"
    assert "exit 1" in first["run"]


def test_login_and_dist_deploys_are_exclusive():
    deploys = [s for s in _steps("deploy") if "wrangler-action" in s.get("uses", "")]
    by_target = {("site" if "pages deploy site" in s["with"]["command"] else "dist"): s for s in deploys}
    assert set(by_target) == {"site", "dist"} and len(deploys) == 2
    assert by_target["dist"]["if"] == "${{ " + LOGIN_OFF + " }}"
    assert "workingDirectory" not in by_target["dist"]["with"]
    assert by_target["site"]["if"] == "${{ " + LOGIN_ON + " }}"
    assert by_target["site"]["with"]["workingDirectory"] == "deploy/pages"
    for step in deploys:
        assert "--branch=main" in step["with"]["command"]


def test_login_mode_steps_run_only_in_login_mode():
    steps = _steps("deploy")
    start = next(i for i, s in enumerate(steps) if "setup-node" in s.get("uses", ""))
    for step in steps[start:]:
        assert step.get("if") == "${{ " + LOGIN_ON + " }}", step.get("name")
    names = [s.get("name", s.get("uses", "")) for s in steps[start:]]
    assert names[-1] == "배포 후 로그인 점검"
    assert "--mode login" in steps[-1]["run"]
    assert not steps[-1].get("continue-on-error")


def test_node_auth_token_only_in_login_install_step():
    holders = [s for job in WF["jobs"] for s in _steps(job) if "NODE_AUTH_TOKEN" in yaml.safe_dump(s)]
    assert len(holders) == 1
    step = holders[0]
    assert step["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert step["run"] == "npm ci" and step["working-directory"] == "deploy/pages"
    assert step in _steps("deploy")


def test_login_site_gets_functions_assets_and_routes():
    prep = next(s for s in _steps("deploy") if s.get("name") == "사이트 준비(로그인 모드)")
    assert prep["working-directory"] == "deploy/pages"
    for fragment in ("cp -R ../../dist/. site/", "test -f site/_synctx/repos.json", "cp _routes.json site/_routes.json",
                     "client/login.ts", "client/admin.ts"):
        assert fragment in prep["run"], fragment


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


def test_functions_csp_matches_static_headers():
    """Functions가 붙이는 CSP는 _headers(빌드)와 같은 값이어야 한다."""
    import importlib

    source = (WORKFLOW.parents[2] / "deploy" / "pages" / "src" / "http.ts").read_text(encoding="utf-8")
    joined = re.sub(r'"\s*\+\s*"', "", source)
    assert f'"{importlib.import_module("static_export.build").CSP}"' in joined
    assert f'"{importlib.import_module("app").RAW_SVG_CSP}"' in joined


def test_unverified_cloudflare_behaviors_are_marked():
    assert "미확인" in RAW
