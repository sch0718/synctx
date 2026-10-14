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


def test_nul_in_src_becomes_block_outcome_not_exception():
    body = BODY.replace("./motion/index.html", "./a%00.html")
    [outcome] = analyze(body, "docs/a.md", POLICY, reader(FILES)).outcomes
    assert outcome.status == "unsupported"


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


def test_render_for_xwiki_keeps_block_inside_list_item():
    body = "1. 첫째\n\n   ```synctx-html\n   src: ./motion/index.html\n   ```\n\n2. 둘째\n"
    out = render_for_xwiki(body, analyze(body, "docs/a.md", POLICY, reader(FILES)))
    assert out.startswith('1. 첫째\n\n   {{html clean="false"}}<iframe class="synctx-html"')
    assert out.endswith("{{/html}}\n\n2. 둘째\n")


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
    manager.resolve_ref.side_effect = lambda ref=None: f"origin/{ref or 'main'}"
    manager.read_resolved.side_effect = lambda path, resolved: read_file(path)
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


from contextlib import contextmanager

import sync_engine
from synctx_common.git_manager import TreeEntry
from state import StateManager


class FakeRepo:
    def __init__(self, files):
        self.files = dict(files)
        self.head = "h1"
        self.diff = []


def fake_manager_class(repo):
    class FakeManager:
        def __init__(self, profile, base_dir):
            self.profile = profile
            self.status = "ready"
            self.last_error = None

        async def ensure(self):
            return None

        @property
        def head_sha_value(self):
            return repo.head

        def diff_since(self, sha):
            return list(repo.diff)

        def list_tree(self, path="", recursive=False, ref=None):
            names = [p for p in sorted(repo.files) if not p.rsplit("/", 1)[-1].startswith(".")]
            return [TreeEntry(name=p.rsplit("/", 1)[-1], path=p, type="file") for p in names]

        def file_paths(self, ref=None):
            return set(repo.files)

        def resolve_ref(self, ref=None):
            return f"origin/{ref or 'main'}"

        def read_resolved(self, path, resolved):
            if path not in repo.files:
                raise subprocess.CalledProcessError(128, ["git", "show", path])
            return repo.files[path].encode("utf-8")

        def read_file(self, path, ref=None):
            return self.read_resolved(path, self.resolve_ref(ref))

        def read_file_text(self, path, ref=None):
            return self.read_file(path, ref).decode("utf-8")

        def file_size(self, path, ref=None):
            return len(self.read_file(path, ref))

    return FakeManager


@contextmanager
def always_locked(*args, **kwargs):
    yield True


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo({MD: MD_TEXT, **FILES})
    monkeypatch.setattr(sync_engine, "GitRepoManager", fake_manager_class(fake))
    monkeypatch.setattr(sync_engine, "repo_lock", always_locked)
    return fake


def published(eng):
    contents = [call.args[3] for call in eng.xwiki_client.upsert_page.await_args_list]
    eng.xwiki_client.upsert_page.reset_mock()
    return contents


@pytest.mark.asyncio
async def test_first_run_full_scan_publishes_iframe(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    [content] = published(eng)
    [inner] = macro_bodies(content)
    assert inner.startswith('<iframe class="synctx-html"')
    state = StateManager(str(tmp_path)).get_state("r")
    assert state.html_dependencies == {MD: ["docs/motion/index.html", "docs/motion/motion.js"]}
    assert state.html_policy_fingerprint != ""


@pytest.mark.asyncio
async def test_dependency_change_republishes_markdown(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    first = published(eng)[0]
    repo.head, repo.diff = "h2", [("M", "docs/motion/motion.js")]
    repo.files["docs/motion/motion.js"] = "window.v = 2;"
    await eng.run_sync(profile())
    [second] = published(eng)
    assert second != first


@pytest.mark.asyncio
async def test_policy_change_without_new_commit_replaces_iframe(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    published(eng)
    await eng.run_sync(profile(enabled=False))
    [content] = published(eng)
    assert "<iframe" not in content
    assert "이 저장소는 HTML 실행이 허용되지 않습니다" in content


@pytest.mark.asyncio
async def test_unchanged_head_and_policy_does_nothing(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    published(eng)
    assert await eng.run_sync(profile()) is None
    assert published(eng) == []


@pytest.mark.asyncio
async def test_state_without_index_triggers_full_scan(tmp_path, monkeypatch, repo):
    from state import RepoState
    StateManager(str(tmp_path)).save_state("r", RepoState(repo_id="r", last_successful_sha="h1"))
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    assert len(published(eng)) == 1


@pytest.mark.asyncio
async def test_missing_file_recovers_when_added(tmp_path, monkeypatch, repo):
    del repo.files["docs/motion/motion.js"]
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    [broken] = published(eng)
    assert "찾을 수 없는 파일" in broken
    repo.files["docs/motion/motion.js"] = "window.v = 1;"
    repo.head, repo.diff = "h2", [("A", "docs/motion/motion.js")]
    await eng.run_sync(profile())
    [fixed] = published(eng)
    assert '<iframe class="synctx-html"' in fixed


@pytest.mark.asyncio
async def test_reference_swap_keeps_index_current(tmp_path, monkeypatch, repo):
    repo.files["docs/motion/index.html"] = INDEX.replace("motion.js", "a.js")
    repo.files["docs/motion/a.js"] = "window.v = 1;"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    published(eng)
    repo.files["docs/motion/index.html"] = INDEX.replace("motion.js", "b.js")
    repo.files["docs/motion/b.js"] = "window.v = 1;"
    del repo.files["docs/motion/a.js"]
    repo.head, repo.diff = "h2", [("M", "docs/motion/index.html"), ("A", "docs/motion/b.js"), ("D", "docs/motion/a.js")]
    await eng.run_sync(profile())
    published(eng)
    deps = StateManager(str(tmp_path)).get_state("r").html_dependencies[MD]
    assert "docs/motion/b.js" in deps and "docs/motion/a.js" not in deps
    repo.files["docs/motion/b.js"] = "window.v = 3;"
    repo.head, repo.diff = "h3", [("M", "docs/motion/b.js")]
    await eng.run_sync(profile())
    [content] = published(eng)
    assert "window.v = 3;" in content


@pytest.mark.asyncio
async def test_two_markdown_files_sharing_entry_are_both_republished(tmp_path, monkeypatch, repo):
    repo.files["docs/other.md"] = BODY
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    assert len(published(eng)) == 2
    repo.files["docs/motion/motion.js"] = "window.v = 9;"
    repo.head, repo.diff = "h2", [("M", "docs/motion/motion.js")]
    await eng.run_sync(profile())
    assert len(published(eng)) == 2


@pytest.mark.asyncio
async def test_bundle_errors_are_reported_separately(tmp_path, monkeypatch, repo):
    del repo.files["docs/motion/motion.js"]
    eng = engine(tmp_path, monkeypatch)
    report = await eng.run_sync(profile())
    assert report.success_count == 1 and report.fail_count == 0
    assert report.html_errors == [
        {"path": MD, "status": "missing_file", "message": "찾을 수 없는 파일: docs/motion/motion.js"}
    ]
    assert report.summary()["html_errors"] == report.html_errors


@pytest.mark.asyncio
async def test_markdown_without_blocks_keeps_plain_content_sha(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    text = "# 블록 없는 문서\n"
    manager = git_manager({"docs/plain.md": text})
    sha = eng._compute_content_sha(manager, "A", "docs/plain.md", profile(), RepoState(repo_id="r"))
    assert sha == hashlib.sha256(text.encode()).hexdigest()


def test_xwiki_macro_stays_on_one_line_for_multiline_bundles():
    files = {**FILES, "docs/motion/motion.js": "window.v = 1;\n\nwindow.w = 2;\n"}
    out = render_for_xwiki(BODY, analyze(BODY, "docs/a.md", POLICY, reader(files)))
    macro_line = next(line for line in out.splitlines() if line.startswith('{{html clean="false"}}'))
    assert macro_line.endswith("{{/html}}")


def trusted_profile(env="XWIKI_HTML_CRED"):
    return RepoProfile(id="r", name="R", url="https://git.test/r.git", auth=RepoAuthConfig(type="none"),
                       html_embed=HtmlEmbedConfig(enabled=True, allowed_origins=[ORIGIN], xwiki_credential_env=env))


def test_trusted_repo_uses_dedicated_xwiki_account(tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv("XWIKI_HTML_CRED", "htmlbot:secret")
    eng = SyncEngine(SynctxConfig(
        targets=TargetsConfig(xwiki=XWikiTargetConfig(base_url="https://xwiki.internal", auth=TargetAuthConfig(type="basic", credential_env="XWIKI_CRED"))),
        sync=SyncConfig(state_path=str(tmp_path)),
    ))
    trusted = eng._xwiki_client_for(trusted_profile())
    assert trusted is not eng.xwiki_client
    assert trusted.client.auth._auth_header == "Basic " + base64.b64encode(b"htmlbot:secret").decode()
    assert eng._xwiki_client_for(trusted_profile()) is trusted
    assert eng._xwiki_client_for(profile(enabled=True)) is eng.xwiki_client
    disabled = trusted_profile().model_copy(update={"html_embed": HtmlEmbedConfig(enabled=False, xwiki_credential_env="XWIKI_HTML_CRED")})
    assert eng._xwiki_client_for(disabled) is eng.xwiki_client


@pytest.mark.asyncio
async def test_trusted_repo_page_is_published_by_dedicated_account(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch)
    trusted_client = MagicMock()
    trusted_client.user = "htmlbot"
    trusted_client.upsert_page = AsyncMock(return_value=True)
    eng._repo_xwiki_clients["XWIKI_HTML_CRED"] = trusted_client
    await eng._process_single_file(trusted_profile(), git_manager({MD: MD_TEXT, **FILES}), "A", MD, "xwiki", "1")
    trusted_client.upsert_page.assert_awaited_once()
    eng.xwiki_client.upsert_page.assert_not_awaited()
    await eng._process_single_file(profile(enabled=False), git_manager({MD: MD_TEXT, **FILES}), "A", MD, "xwiki", "1")
    eng.xwiki_client.upsert_page.assert_awaited_once()


def test_effective_sha_changes_when_only_rendering_changes(monkeypatch):
    import embed_sync
    before = effective_sha(BODY, analyze(BODY, "docs/a.md", POLICY, reader(FILES)), "fp")
    original = embed_sync.to_iframe
    monkeypatch.setattr(embed_sync, "to_iframe", lambda *a, **k: original(*a, **k).replace("border:0", "border:1px"))
    after = effective_sha(BODY, analyze(BODY, "docs/a.md", POLICY, reader(FILES)), "fp")
    assert before != after


PLAIN = "docs/plain.md"


def dedicated_client(eng, env="XWIKI_HTML_CRED", user="htmlbot"):
    client = MagicMock()
    client.user = user
    client.upsert_page = AsyncMock(return_value=True)
    eng._repo_xwiki_clients[env] = client
    return client


def pages(client):
    """페이지 이름 → 작성자 강제 갱신 여부."""
    result = {call.args[1]: call.kwargs.get("force_author", False) for call in client.upsert_page.await_args_list}
    client.upsert_page.reset_mock()
    return result


def publishers(tmp_path):
    return {path: sf.publisher for path, sf in StateManager(str(tmp_path)).get_state("r").synced_files.items()}


@pytest.mark.asyncio
async def test_revoking_trust_forces_author_change_on_every_page(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n\n{{velocity}}$xcontext.user{{/velocity}}\n"
    eng = engine(tmp_path, monkeypatch)
    trusted = dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    assert set(pages(trusted)) == {"reconfig", "plain"}
    assert publishers(tmp_path) == {MD: "htmlbot", PLAIN: "htmlbot"}

    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client) == {"reconfig": True, "plain": True}
    assert publishers(tmp_path) == {MD: "", PLAIN: ""}


@pytest.mark.asyncio
async def test_changing_dedicated_account_forces_author_change(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    pages(dedicated_client(eng, "XWIKI_HTML_CRED", "htmlbot"))
    await eng.run_sync(trusted_profile())

    second = dedicated_client(eng, "XWIKI_HTML_CRED2", "htmlbot2")
    await eng.run_sync(trusted_profile("XWIKI_HTML_CRED2"))

    assert pages(second) == {"reconfig": True, "plain": True}


@pytest.mark.asyncio
async def test_unchanged_publisher_does_not_republish(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    trusted = dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    pages(trusted)
    assert await eng.run_sync(trusted_profile()) is None
    assert pages(trusted) == {}


@pytest.mark.asyncio
async def test_state_without_recorded_publisher_is_reconciled(tmp_path, monkeypatch, repo):
    import json
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    pages(eng.xwiki_client)
    state_file = tmp_path / "r.json"
    data = json.loads(state_file.read_text("utf-8"))
    for entry in data["synced_files"].values():
        entry.pop("publisher")
    state_file.write_text(json.dumps(data), "utf-8")

    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client) == {"reconfig": True, "plain": True}
    assert publishers(tmp_path) == {MD: "", PLAIN: ""}


def test_state_publisher_migration():
    synced = {"docs/a.md": {"content_sha": "s", "target": "xwiki", "external_key": "r:docs/a.md", "resource_id": "xwiki:docs.a"}}
    pre_feature = RepoState.from_dict({"schema_version": 1, "repo_id": "r", "synced_files": synced})
    assert pre_feature.synced_files["docs/a.md"].publisher == ""
    feature_build = RepoState.from_dict({"schema_version": 1, "repo_id": "r", "synced_files": synced, "html_dependencies": {}})
    assert feature_build.synced_files["docs/a.md"].publisher is None
    restored = RepoState.from_dict(feature_build.to_dict())
    assert restored.synced_files["docs/a.md"].publisher is None


def test_xwiki_client_exposes_basic_auth_user(monkeypatch):
    from targets.xwiki import XWikiClient
    monkeypatch.setenv("XWIKI_HTML_CRED", "htmlbot:se:cret")
    client = XWikiClient(XWikiTargetConfig(base_url="https://xwiki.internal", auth=TargetAuthConfig(type="basic", credential_env="XWIKI_HTML_CRED")))
    assert client.user == "htmlbot"


def xwiki_with_page(existing):
    import httpx
    from targets.xwiki import XWikiClient
    puts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=existing) if existing else httpx.Response(404)
        puts.append(__import__("json").loads(request.content)["content"])
        return httpx.Response(201)

    client = XWikiClient(XWikiTargetConfig(base_url="https://xwiki.internal", auth=TargetAuthConfig(type="basic", credential_env="XWIKI_UNSET")))
    client.client = httpx.AsyncClient(base_url="https://xwiki.internal", transport=httpx.MockTransport(handler))
    return client, puts


@pytest.mark.asyncio
async def test_force_author_saves_twice_when_content_is_identical():
    client, puts = xwiki_with_page({"title": "T", "content": "body"})
    assert await client.upsert_page("docs", "a", "T", "body", force_author=True) is True
    assert len(puts) == 2 and puts[0] != "body" and puts[1] == "body"


@pytest.mark.asyncio
async def test_force_author_saves_once_when_content_differs_or_page_is_new():
    client, puts = xwiki_with_page({"title": "T", "content": "old"})
    await client.upsert_page("docs", "a", "T", "new", force_author=True)
    assert puts == ["new"]
    client, puts = xwiki_with_page(None)
    await client.upsert_page("docs", "a", "T", "new", force_author=True)
    assert puts == ["new"]


@pytest.mark.asyncio
async def test_identical_content_without_force_is_skipped():
    client, puts = xwiki_with_page({"title": "T", "content": "body"})
    assert await client.upsert_page("docs", "a", "T", "body") is False
    assert puts == []


@pytest.mark.asyncio
async def test_force_author_saves_twice_when_only_title_changes():
    client, puts = xwiki_with_page({"title": "Old", "content": "body"})
    await client.upsert_page("docs", "a", "New", "body", force_author=True)
    assert len(puts) == 2 and puts[0] != "body" and puts[1] == "body"


@pytest.mark.asyncio
async def test_failed_xwiki_write_while_changing_account_is_retried_after_revert(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    pages(eng.xwiki_client)

    trusted = dedicated_client(eng)

    async def partial(space, page, title, content, force_author=False):
        if page == "plain":
            raise RuntimeError("403 Forbidden after temporary save")
        return True

    trusted.upsert_page.side_effect = partial
    await eng.run_sync(trusted_profile())
    assert publishers(tmp_path)[PLAIN] is None

    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client) == {"reconfig": True, "plain": True}
    assert publishers(tmp_path) == {MD: "", PLAIN: ""}


@pytest.mark.asyncio
async def test_xwiki_success_with_openproject_failure_records_xwiki_publisher(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "---\ntitle: 일반\nsynctx:\n  target: both\n---\n\n# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch, op=True)
    trusted = dedicated_client(eng)
    eng.op_client.upsert_work_package = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
    await eng.run_sync(trusted_profile())
    assert "plain" in pages(trusted)
    assert publishers(tmp_path)[PLAIN] == "htmlbot"

    eng.op_client.upsert_work_package = AsyncMock(return_value=True)
    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client)["plain"] is True
    assert publishers(tmp_path)[PLAIN] == ""


@pytest.mark.asyncio
async def test_account_change_is_marked_unknown_on_disk_before_xwiki_write(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    trusted = dedicated_client(eng)
    seen = {}

    async def record(space, page, title, content, force_author=False):
        seen[page] = publishers(tmp_path).get(f"docs/{page}.md", "absent")
        return True

    trusted.upsert_page.side_effect = record
    await eng.run_sync(trusted_profile())

    assert seen == {"reconfig": None, "plain": None}
    assert publishers(tmp_path) == {MD: "htmlbot", PLAIN: "htmlbot"}


def remote_pages(client, pages_by_name):
    async def get_page(space, page):
        return pages_by_name.get(page)
    client.get_page = AsyncMock(side_effect=get_page)


def synced(tmp_path):
    return StateManager(str(tmp_path)).get_state("r").synced_files


async def delete_plain(eng, repo, profile_):
    repo.files.pop(PLAIN)
    repo.head, repo.diff = "h2", [("D", PLAIN)]
    await eng.run_sync(profile_)


@pytest.mark.asyncio
async def test_kept_page_of_deleted_markdown_is_reclaimed_by_default_account(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    trusted = dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    await delete_plain(eng, repo, trusted_profile())
    assert synced(tmp_path)[PLAIN].xwiki_orphan is True
    assert synced(tmp_path)[PLAIN].publisher == "htmlbot"
    pages(trusted)

    remote_pages(eng.xwiki_client, {"plain": {"title": "plain", "rawTitle": "plain", "content": "edited in wiki"}})
    await eng.run_sync(profile(enabled=False))

    calls = [c for c in eng.xwiki_client.upsert_page.await_args_list if c.args[1] == "plain"]
    assert [(c.args[2], c.args[3], c.kwargs["force_author"]) for c in calls] == [("plain", "edited in wiki", True)]
    assert synced(tmp_path)[PLAIN].publisher == ""
    assert pages(trusted) == {}
    eng.xwiki_client.upsert_page.reset_mock()
    assert await eng.run_sync(profile(enabled=False)) is None


@pytest.mark.asyncio
async def test_kept_page_is_reclaimed_by_default_account_on_account_change(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    await delete_plain(eng, repo, trusted_profile())

    second = dedicated_client(eng, "XWIKI_HTML_CRED2", "htmlbot2")
    remote_pages(eng.xwiki_client, {"plain": {"title": "plain", "content": "body"}})
    await eng.run_sync(trusted_profile("XWIKI_HTML_CRED2"))

    assert "plain" not in pages(second)
    assert pages(eng.xwiki_client) == {"plain": True}
    assert synced(tmp_path)[PLAIN].publisher == ""


@pytest.mark.asyncio
async def test_kept_page_already_gone_drops_the_record(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    await delete_plain(eng, repo, trusted_profile())

    remote_pages(eng.xwiki_client, {})
    await eng.run_sync(profile(enabled=False))

    assert PLAIN not in synced(tmp_path)


@pytest.mark.asyncio
async def test_deleted_page_or_default_account_page_drops_the_record(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    await delete_plain(eng, repo, profile(enabled=False))
    assert PLAIN not in synced(tmp_path)

    repo.files[PLAIN] = "# 일반 문서\n"
    repo.head, repo.diff = "h3", [("A", PLAIN)]
    dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    eng.config.sync.on_delete = "delete"
    eng.xwiki_client.delete_page = AsyncMock(return_value=True)
    trusted = eng._repo_xwiki_clients["XWIKI_HTML_CRED"]
    trusted.delete_page = AsyncMock(return_value=True)
    repo.files.pop(PLAIN)
    repo.head, repo.diff = "h4", [("D", PLAIN)]
    await eng.run_sync(trusted_profile())
    trusted.delete_page.assert_awaited_once()
    assert PLAIN not in synced(tmp_path)


@pytest.mark.asyncio
async def test_readded_markdown_leaves_kept_state(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    trusted = dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    await delete_plain(eng, repo, trusted_profile())
    pages(trusted)

    repo.files[PLAIN] = "# 일반 문서\n"
    repo.head, repo.diff = "h3", [("A", PLAIN)]
    await eng.run_sync(trusted_profile())

    assert pages(trusted) == {"plain": False}
    assert synced(tmp_path)[PLAIN].xwiki_orphan is False
    assert synced(tmp_path)[PLAIN].publisher == "htmlbot"


@pytest.mark.asyncio
async def test_readding_markdown_while_revoking_forces_author_change(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    await delete_plain(eng, repo, trusted_profile())
    remote_pages(eng.xwiki_client, {})

    repo.files[PLAIN] = "# 일반 문서\n"
    repo.head, repo.diff = "h3", [("A", PLAIN)]
    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client)["plain"] is True
    eng.xwiki_client.get_page.assert_not_awaited()
    assert synced(tmp_path)[PLAIN].publisher == ""
    assert synced(tmp_path)[PLAIN].xwiki_orphan is False


OP_FRONT = "---\ntitle: 일반\nsynctx:\n  target: {target}\n---\n\n# 일반 문서\n"


@pytest.mark.asyncio
async def test_switching_target_to_openproject_keeps_xwiki_page_for_reclaim(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = OP_FRONT.format(target="xwiki")
    eng = engine(tmp_path, monkeypatch, op=True)
    trusted = dedicated_client(eng)
    await eng.run_sync(trusted_profile())
    pages(trusted)

    repo.files[PLAIN] = OP_FRONT.format(target="openproject")
    repo.head, repo.diff = "h2", [("M", PLAIN)]
    await eng.run_sync(trusted_profile())
    record = synced(tmp_path)[PLAIN]
    assert record.xwiki_orphan is True and record.publisher == "htmlbot"
    assert "openproject:" in record.resource_id and "xwiki:" in record.resource_id
    assert pages(trusted) == {}
    assert await eng.run_sync(trusted_profile()) is None

    remote_pages(eng.xwiki_client, {"plain": {"title": "일반", "content": "body"}})
    await eng.run_sync(profile(enabled=False))

    assert pages(eng.xwiki_client) == {"reconfig": True, "plain": True}
    eng.xwiki_client.get_page.assert_awaited_once_with("docs", "plain")
    assert synced(tmp_path)[PLAIN].publisher == ""


@pytest.mark.asyncio
async def test_switching_target_to_openproject_drops_default_account_xwiki_tracking(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = OP_FRONT.format(target="xwiki")
    eng = engine(tmp_path, monkeypatch, op=True)
    await eng.run_sync(profile(enabled=False))
    repo.files[PLAIN] = OP_FRONT.format(target="openproject")
    repo.head, repo.diff = "h2", [("M", PLAIN)]
    await eng.run_sync(profile(enabled=False))
    record = synced(tmp_path)[PLAIN]
    assert record.xwiki_orphan is False and "xwiki:" not in record.resource_id


@pytest.mark.asyncio
async def test_failed_openproject_part_is_retried_after_xwiki_success(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = OP_FRONT.format(target="both")
    eng = engine(tmp_path, monkeypatch, op=True)
    await eng.run_sync(profile(enabled=False))
    dedicated_client(eng)
    eng.op_client.upsert_work_package = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
    await eng.run_sync(trusted_profile())
    assert [pf.path for pf in StateManager(str(tmp_path)).get_state("r").pending_failures] == [PLAIN]

    eng.op_client.upsert_work_package = AsyncMock(return_value=True)
    await eng.run_sync(trusted_profile())

    eng.op_client.upsert_work_package.assert_awaited_once()
    assert StateManager(str(tmp_path)).get_state("r").pending_failures == []


@pytest.mark.asyncio
async def test_validation_failures_wait_for_content_change(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    state = StateManager(str(tmp_path)).get_state("r")
    StateManager(str(tmp_path)).add_pending_failure(state, PLAIN, "validation_error", "invalid frontmatter")
    StateManager(str(tmp_path)).save_state("r", state)
    pages(eng.xwiki_client)

    assert await eng.run_sync(profile(enabled=False)) is None
    assert pages(eng.xwiki_client) == {}


def test_state_migrates_deleted_flag_to_xwiki_orphan():
    for old, new in ((True, True), (False, False)):
        entry = {"content_sha": "", "target": "xwiki", "external_key": "r:docs/a.md", "resource_id": "xwiki:docs.a",
                 "publisher": "htmlbot", "deleted": old}
        restored = RepoState.from_dict({"repo_id": "r", "synced_files": {"docs/a.md": entry}, "html_dependencies": {}})
        assert restored.synced_files["docs/a.md"].xwiki_orphan is new
        assert "deleted" not in restored.to_dict()["synced_files"]["docs/a.md"]


@pytest.mark.asyncio
async def test_missing_tracked_file_is_processed_as_delete(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    state = StateManager(str(tmp_path)).get_state("r")
    StateManager(str(tmp_path)).add_pending_failure(state, PLAIN, "transient_error", "timeout")
    StateManager(str(tmp_path)).save_state("r", state)
    repo.files.pop(PLAIN)
    eng.config.sync.on_delete = "delete"
    eng.xwiki_client.delete_page = AsyncMock(return_value=True)

    await eng.run_sync(profile(enabled=False))

    eng.xwiki_client.delete_page.assert_awaited_once_with("docs", "plain")
    after = StateManager(str(tmp_path)).get_state("r")
    assert PLAIN not in after.synced_files and after.pending_failures == []


@pytest.mark.asyncio
async def test_git_listing_error_keeps_pending_failure(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = "# 일반 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    state = StateManager(str(tmp_path)).get_state("r")
    StateManager(str(tmp_path)).add_pending_failure(state, PLAIN, "transient_error", "timeout")
    StateManager(str(tmp_path)).save_state("r", state)
    manager_class = sync_engine.GitRepoManager

    def broken(self, ref=None):
        raise subprocess.CalledProcessError(128, ["git", "ls-tree"], stderr=b"fatal: unable to read tree")

    monkeypatch.setattr(manager_class, "file_paths", broken)
    with pytest.raises(subprocess.CalledProcessError):
        await eng.run_sync(profile(enabled=False))
    assert [pf.path for pf in StateManager(str(tmp_path)).get_state("r").pending_failures] == [PLAIN]


@pytest.mark.asyncio
async def test_page_of_markdown_deleted_after_partial_first_publish_is_reclaimed(tmp_path, monkeypatch, repo):
    repo.files[PLAIN] = OP_FRONT.format(target="both")
    eng = engine(tmp_path, monkeypatch, op=True)
    trusted = dedicated_client(eng)
    eng.op_client.upsert_work_package = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
    await eng.run_sync(trusted_profile())
    first = StateManager(str(tmp_path)).get_state("r")
    assert first.last_successful_sha is None and first.synced_files[PLAIN].publisher == "htmlbot"
    pages(trusted)

    repo.files.pop(PLAIN)
    repo.head = "h2"
    eng.op_client.upsert_work_package = AsyncMock(return_value=True)
    remote_pages(eng.xwiki_client, {"plain": {"title": "일반", "content": "body"}})
    await eng.run_sync(profile(enabled=False))

    eng.xwiki_client.get_page.assert_awaited_once_with("docs", "plain")
    assert pages(eng.xwiki_client)["plain"] is True
    record = synced(tmp_path)[PLAIN]
    assert record.xwiki_orphan is True and record.publisher == ""


@pytest.mark.asyncio
async def test_existing_hidden_markdown_is_not_treated_as_deleted(tmp_path, monkeypatch, repo):
    hidden = "docs/.hidden.md"
    repo.files[hidden] = "# 숨김 문서\n"
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(enabled=False))
    await eng._process_with_retry(
        profile(enabled=False), sync_engine.GitRepoManager(profile(), str(tmp_path)), "A", hidden, "xwiki", "1",
        eng._get_retry_config("xwiki"), sync_engine.SyncReport(), state := StateManager(str(tmp_path)).get_state("r"),
        "h1", "sha",
    )
    StateManager(str(tmp_path)).add_pending_failure(state, hidden, "transient_error", "timeout")
    StateManager(str(tmp_path)).save_state("r", state)
    pages(eng.xwiki_client)
    eng.config.sync.on_delete = "delete"
    eng.xwiki_client.delete_page = AsyncMock(return_value=True)

    await eng.run_sync(profile(enabled=False))

    eng.xwiki_client.delete_page.assert_not_awaited()
    assert ".hidden" in pages(eng.xwiki_client)
    assert StateManager(str(tmp_path)).get_state("r").pending_failures == []
