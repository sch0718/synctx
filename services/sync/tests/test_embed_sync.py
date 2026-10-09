import hashlib
import re
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from synctx_common.config import HtmlEmbedConfig
from embed_sync import analyze, effective_sha, policy_fingerprint, render_for_openproject, render_for_xwiki
from state import RepoState

ORIGIN = "https://cdn.synctx.local"
POLICY = HtmlEmbedConfig(enabled=True, allowed_origins=[ORIGIN])
BODY = "# 재구성\n\n```synctx-html\nsrc: ./motion/index.html\n```\n\n끝\n"
INDEX = '<!doctype html><html><head><title>모션</title></head><body><script src="motion.js"></script></body></html>'
FILES = {"docs/motion/index.html": INDEX, "docs/motion/motion.js": "window.v = 1;"}


def reader(files):
    def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path].encode("utf-8")
    return read


def macro_bodies(text):
    return re.findall(r'\{\{html clean="false"\}\}(.*?)\{\{/html\}\}', text, re.DOTALL)


def test_analyze_ok_block():
    analysis = analyze(BODY, "docs/a.md", POLICY, reader(FILES))
    [outcome] = analysis.outcomes
    assert outcome.status == "ok"
    assert outcome.fragment.startswith('<iframe class="synctx-html" sandbox="allow-scripts"')
    assert analysis.deps == ["docs/motion/index.html", "docs/motion/motion.js"]


def test_analyze_disabled_and_missing():
    disabled = analyze(BODY, "docs/a.md", HtmlEmbedConfig(), reader(FILES)).outcomes[0]
    assert disabled.status == "disabled"
    assert disabled.deps == ("docs/motion/index.html",)
    missing = analyze(BODY, "docs/a.md", POLICY, reader({"docs/motion/index.html": INDEX})).outcomes[0]
    assert missing.status == "missing_file"
    assert missing.deps == ("docs/motion/index.html", "docs/motion/motion.js")


def test_malformed_url_becomes_block_outcome_not_exception():
    files = {**FILES, "docs/motion/index.html": INDEX.replace("</body>", '<img src="https://["></body>')}
    body = BODY + "\n```synctx-html\nsrc: ./other/index.html\n```\n"
    files["docs/other/index.html"] = "<p>ok</p>"
    analysis = analyze(body, "docs/a.md", POLICY, reader(files))
    assert [o.status for o in analysis.outcomes] == ["disallowed_host", "ok"]


def test_render_for_xwiki_is_single_macro_without_braces():
    body = BODY.replace("./motion/index.html", '"./{{/html}}/index.html"')
    out = render_for_xwiki(body, analyze(body, "docs/a.md", POLICY, reader(FILES)))
    [inner] = macro_bodies(out)
    assert "{" not in inner and "}" not in inner
    assert "&#123;&#123;/html&#125;&#125;" in inner
    assert "```synctx-html" not in out


def test_render_for_openproject():
    out = render_for_openproject(BODY, "https://synctx.synctx.local/view/r/docs/a.md")
    assert "> 인터랙티브 콘텐츠: [Viewer에서 보기](https://synctx.synctx.local/view/r/docs/a.md)" in out
    assert "```synctx-html" not in out


def test_effective_sha_without_blocks_is_plain_sha():
    content = "# 그냥 문서\n"
    analysis = analyze(content, "docs/b.md", POLICY, reader({}))
    assert effective_sha(content, analysis, "fp") == hashlib.sha256(content.encode()).hexdigest()


def test_effective_sha_depends_on_bundle_deps_and_policy():
    a = analyze(BODY, "docs/a.md", POLICY, reader(FILES))
    changed = analyze(BODY, "docs/a.md", POLICY, reader({**FILES, "docs/motion/motion.js": "window.v = 2;"}))
    assert effective_sha(BODY, a, "fp") != effective_sha(BODY, changed, "fp")
    assert effective_sha(BODY, a, "fp") != effective_sha(BODY, a, "fp2")


def test_policy_fingerprint_is_stable_and_sensitive():
    assert policy_fingerprint(POLICY) == policy_fingerprint(HtmlEmbedConfig(enabled=True, allowed_origins=[ORIGIN]))
    assert policy_fingerprint(POLICY) != policy_fingerprint(HtmlEmbedConfig(enabled=False, allowed_origins=[ORIGIN]))


def test_state_round_trip_keeps_html_fields():
    state = RepoState(repo_id="r", html_dependencies={"docs/a.md": ["docs/motion/index.html"]}, html_policy_fingerprint="fp")
    restored = RepoState.from_dict(state.to_dict())
    assert restored.html_dependencies == {"docs/a.md": ["docs/motion/index.html"]}
    assert restored.html_policy_fingerprint == "fp"
    assert RepoState.from_dict({"schema_version": 1, "repo_id": "r"}).html_dependencies is None

from synctx_common.config import (
    OpenProjectTargetConfig, RepoAuthConfig, RepoProfile, RuntimeConfig, SyncConfig, SynctxConfig,
    TargetAuthConfig, TargetsConfig, XWikiTargetConfig, PlatformHtmlEmbedConfig,
)
from sync_engine import SyncEngine

MD = "docs/reconfig.md"
MD_TEXT = "---\ntitle: 재구성\n---\n\n" + BODY


def git_manager(files):
    manager = MagicMock()

    def read_file(path, ref=None):
        if path not in files:
            raise subprocess.CalledProcessError(128, ["git", "show", path])
        return files[path].encode("utf-8")

    manager.read_file.side_effect = read_file
    manager.read_file_text.side_effect = lambda path, ref=None: read_file(path, ref).decode("utf-8")
    return manager


def profile(enabled=True):
    return RepoProfile(id="r", name="R", url="https://git.test/r.git", auth=RepoAuthConfig(type="none"),
                       html_embed=HtmlEmbedConfig(enabled=enabled, allowed_origins=[ORIGIN]))


def engine(tmp_path, monkeypatch, op=False):
    monkeypatch.setenv("XWIKI_CRED", "user:pass")
    monkeypatch.setenv("OP_TOKEN", "tok")
    targets = TargetsConfig(
        xwiki=XWikiTargetConfig(base_url="https://xwiki.internal", auth=TargetAuthConfig(type="basic", credential_env="XWIKI_CRED")),
        openproject=OpenProjectTargetConfig(base_url="https://op.internal", auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN")) if op else None,
    )
    eng = SyncEngine(SynctxConfig(
        runtime=RuntimeConfig(public_base_url="https://synctx.synctx.local"),
        html_embed=PlatformHtmlEmbedConfig(allowed_origins=[ORIGIN]),
        targets=targets,
        sync=SyncConfig(state_path=str(tmp_path)),
    ))
    eng.xwiki_client = MagicMock()
    eng.xwiki_client.upsert_page = AsyncMock(return_value=True)
    eng.op_client = MagicMock()
    eng.op_client.upsert_work_package = AsyncMock(return_value=True)
    eng.op_client.get_version_by_name = AsyncMock(return_value=None)
    return eng


@pytest.mark.asyncio
async def test_process_single_file_publishes_macro(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch)
    files = {MD: MD_TEXT, **FILES}
    await eng._process_single_file(profile(), git_manager(files), "A", MD, "xwiki", "1")
    content = eng.xwiki_client.upsert_page.await_args.args[3]
    [inner] = macro_bodies(content)
    assert inner.startswith('<iframe class="synctx-html" sandbox="allow-scripts"')
    assert "{" not in inner and "}" not in inner


@pytest.mark.asyncio
async def test_process_single_file_openproject_gets_link(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch, op=True)
    files = {MD: MD_TEXT, **FILES}
    await eng._process_single_file(profile(), git_manager(files), "A", MD, "openproject", "1")
    payload = eng.op_client.upsert_work_package.await_args.args[2]
    raw = payload["description"]["raw"]
    assert "> 인터랙티브 콘텐츠: [Viewer에서 보기](https://synctx.synctx.local/view/r/docs/reconfig.md)" in raw
    assert "```synctx-html" not in raw


@pytest.mark.asyncio
async def test_bundle_failure_counts_metric(tmp_path, monkeypatch):
    from synctx_common.observability import html_bundle_total, sync_failures_total
    eng = engine(tmp_path, monkeypatch)
    files = {MD: MD_TEXT, "docs/motion/index.html": INDEX}
    failures = sync_failures_total.labels(repo_id="r", target="xwiki", error_type="html_bundle")
    missing = html_bundle_total.labels(service="sync", repo_id="r", result="missing_file")
    before = (failures._value.get(), missing._value.get())
    await eng._process_single_file(profile(), git_manager(files), "A", MD, "xwiki", "1")
    assert (failures._value.get(), missing._value.get()) == (before[0] + 1, before[1] + 1)
    assert "인터랙티브 콘텐츠를 불러오지 못했습니다" in eng.xwiki_client.upsert_page.await_args.args[3]
