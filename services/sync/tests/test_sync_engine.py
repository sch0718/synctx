"""SyncEngine 통합 테스트 (외부 의존성 mock)."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import pytest

from synctx_common.config import (
    SynctxConfig, TargetsConfig, XWikiTargetConfig, OpenProjectTargetConfig,
    TargetAuthConfig, TargetRetryConfig, RepoProfile, RepoAuthConfig,
    MappingRule, TargetMatchConfig, OpenProjectTargetMapping, SyncConfig,
)
from sync_engine import SyncEngine, SyncReport, SyncError, classify_error, should_retry, can_advance_state
from state import RepoState


def _xwiki_config():
    return XWikiTargetConfig(
        base_url="https://xwiki.internal",
        auth=TargetAuthConfig(type="basic", credential_env="XWIKI_CRED"),
    )


def _op_config():
    return OpenProjectTargetConfig(
        base_url="https://op.internal",
        auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
        type_map={"task": 1, "doc": 2},
        status_map={"open": 1, "closed": 6},
        priority_map={"high": 1, "low": 3},
    )


def _make_config(**overrides):
    defaults = {
        "targets": TargetsConfig(xwiki=_xwiki_config()),
        "sync": SyncConfig(state_path="/tmp/test-sync-state"),
    }
    defaults.update(overrides)
    return SynctxConfig(**defaults)


def _make_repo():
    return RepoProfile(
        id="test-repo",
        name="Test Repo",
        url="https://git.internal/test.git",
        auth=RepoAuthConfig(type="none"),
    )


class TestSyncReport:
    def test_empty_report(self):
        r = SyncReport()
        assert r.success_count == 0
        assert r.fail_count == 0

    def test_record_success(self):
        r = SyncReport()
        r.record_success("docs/a.md")
        assert r.success_count == 1

    def test_record_failure(self):
        r = SyncReport()
        r.record_failure("docs/b.md", "timeout", "xwiki", "transient_error")
        assert r.fail_count == 1
        assert r.failed[0]["error"] == "timeout"
        assert r.failed[0]["error_type"] == "transient_error"

    def test_summary(self):
        r = SyncReport()
        r.record_success("a.md")
        r.record_failure("b.md", "err", "xwiki", "auth_error")
        r.record_skip("c.md")
        s = r.summary()
        assert s["success"] == 1
        assert s["failed"] == 1
        assert s["skipped"] == 1


class TestErrorClassification:
    @pytest.mark.parametrize("status,expected", [
        (503, "transient_error"), (404, "transient_error"),
        (401, "auth_error"), (409, "conflict_error"),
    ])
    def test_http_status_takes_precedence_over_url(self, status, expected):
        error = httpx.Response(status, request=httpx.Request("GET", "https://op.internal/invalid"))
        with pytest.raises(httpx.HTTPStatusError) as raised:
            error.raise_for_status()
        assert classify_error(raised.value) == expected

    def test_auth_error_401(self):
        assert classify_error(Exception("HTTP 401 Unauthorized")) == "auth_error"

    def test_auth_error_403(self):
        assert classify_error(Exception("403 Forbidden")) == "auth_error"

    def test_transient_error_timeout(self):
        assert classify_error(Exception("connection timeout")) == "transient_error"

    def test_transient_error_429(self):
        assert classify_error(Exception("429 Too Many Requests")) == "transient_error"

    def test_conflict_error(self):
        assert classify_error(Exception("409 Conflict: duplicate external key")) == "conflict_error"

    def test_validation_error(self):
        assert classify_error(Exception("frontmatter validation failed")) == "validation_error"

    def test_mapping_error(self):
        assert classify_error(Exception("Unknown type: nonexistent")) == "mapping_error"

    def test_sync_error_preserves_type(self):
        assert classify_error(SyncError("msg", "conflict_error")) == "conflict_error"

    def test_should_retry_only_transient(self):
        assert should_retry("transient_error") is True
        assert should_retry("auth_error") is False
        assert should_retry("mapping_error") is False
        assert should_retry("validation_error") is False
        assert should_retry("conflict_error") is False

    def test_can_advance_state_only_validation(self):
        assert can_advance_state("validation_error") is True
        assert can_advance_state("transient_error") is False
        assert can_advance_state("auth_error") is False
        assert can_advance_state("mapping_error") is False


@pytest.mark.asyncio
async def test_openproject_lookup_failure_is_reprocessed_next_run(tmp_path, monkeypatch):
    config = _make_config(
        targets=TargetsConfig(openproject=_op_config()),
        sync=SyncConfig(state_path=str(tmp_path)),
        mappings=[MappingRule(
            match=TargetMatchConfig(path_prefix="issues/", extensions=[".md"]),
            target="openproject",
            openproject=OpenProjectTargetMapping(project="42"),
        )],
    )
    engine = SyncEngine(config)
    path = "issues/invalid.md"
    git = MagicMock(status="ready", head_sha_value="new-head", ensure=AsyncMock())
    git.list_tree.return_value = [SimpleNamespace(type="file", name="invalid.md", path=path)]
    git.file_paths.return_value = {path}
    git.read_file_text.return_value = "# Task"
    monkeypatch.setattr("sync_engine.GitRepoManager", lambda *_: git)
    monkeypatch.setattr("sync_engine.repo_lock", lambda *_: nullcontext(MagicMock()))
    monkeypatch.setattr(engine, "_get_retry_config", lambda _: TargetRetryConfig(max_attempts=1))
    request = httpx.Request("GET", "https://op.internal/api/v3/work_packages?filters=invalid")
    engine.op_client.client.get = AsyncMock(side_effect=[
        httpx.Response(503, request=request),
        httpx.Response(200, request=request, json={"_embedded": {"elements": []}}),
    ])
    engine.op_client.client.post = AsyncMock(return_value=MagicMock())
    repo = _make_repo()

    try:
        first = await engine.run_sync(repo)
        engine.op_client.client.post.assert_not_awaited()
        assert first.failed[0]["error_type"] == "transient_error"
        state = engine.state_manager.get_state(repo.id)
        assert state.last_successful_sha is None
        assert state.pending_failures[0].path == path

        second = await engine.run_sync(repo)
        assert second.success == [path]
        engine.op_client.client.post.assert_awaited_once()
        state = engine.state_manager.get_state(repo.id)
        assert state.last_successful_sha == "new-head" and not state.pending_failures
    finally:
        await engine.close()


class TestResolveTarget:
    def test_default_xwiki(self):
        config = _make_config()
        engine = SyncEngine(config)
        target, project_id = engine._resolve_target("docs/readme.md")
        assert target == "xwiki"

    def test_mapping_rule(self):
        config = _make_config(
            targets=TargetsConfig(
                xwiki=_xwiki_config(),
                openproject=_op_config(),
            ),
            mappings=[
                MappingRule(
                    match=TargetMatchConfig(path_prefix="issues/", extensions=[".md"]),
                    target="openproject",
                    openproject=OpenProjectTargetMapping(project="42"),
                ),
            ],
        )
        engine = SyncEngine(config)
        target, project_id = engine._resolve_target("issues/TASK-001.md")
        assert target == "openproject"
        assert project_id == "42"


class TestDeriveXwikiTarget:
    def test_single_dir(self):
        config = _make_config()
        engine = SyncEngine(config)
        space, page = engine._derive_xwiki_target(_make_repo(), "docs/readme.md")
        assert space == "docs"
        assert page == "readme"

    def test_nested_dirs(self):
        config = _make_config()
        engine = SyncEngine(config)
        space, page = engine._derive_xwiki_target(_make_repo(), "docs/arch/overview.md")
        assert space == "docs.arch"
        assert page == "overview"

    def test_root_file(self):
        config = _make_config()
        engine = SyncEngine(config)
        repo = _make_repo()
        space, page = engine._derive_xwiki_target(repo, "readme.md")
        assert space == repo.id
        assert page == "readme"

    def test_deep_nesting(self):
        config = _make_config()
        engine = SyncEngine(config)
        space, page = engine._derive_xwiki_target(_make_repo(), "a/b/c/d/file.md")
        assert space == "a.b.c.d"
        assert page == "file"


class TestBuildOpPayload:
    def test_basic_payload(self):
        config = _make_config(
            targets=TargetsConfig(xwiki=_xwiki_config(), openproject=_op_config()),
        )
        engine = SyncEngine(config)

        fm = {"title": "My Task", "synctx": {"type": "task", "status": "open", "priority": "high"}}
        payload = engine._build_op_payload(fm, "body text", "issues/task.md")
        assert payload["subject"] == "My Task"
        assert "/api/v3/types/1" in payload["_links"]["type"]["href"]
        assert "/api/v3/statuses/1" in payload["_links"]["status"]["href"]
        assert "/api/v3/priorities/1" in payload["_links"]["priority"]["href"]

    def test_unknown_mapping_raises_sync_error(self):
        config = _make_config(
            targets=TargetsConfig(xwiki=_xwiki_config(), openproject=_op_config()),
        )
        engine = SyncEngine(config)
        fm = {"title": "Task", "synctx": {"type": "nonexistent"}}
        with pytest.raises(SyncError) as exc_info:
            engine._build_op_payload(fm, "body", "task.md")
        assert exc_info.value.error_type == "mapping_error"

    def test_due_date(self):
        config = _make_config(
            targets=TargetsConfig(xwiki=_xwiki_config(), openproject=_op_config()),
        )
        engine = SyncEngine(config)
        fm = {"synctx": {"due_date": "2026-12-31"}}
        payload = engine._build_op_payload(fm, "body", "task.md")
        assert payload["dueDate"] == "2026-12-31"

    def test_assignee_mapping(self):
        op = _op_config()
        op.assignee_map = {"alice": 42, "bob": 99}
        config = _make_config(
            targets=TargetsConfig(xwiki=_xwiki_config(), openproject=op),
        )
        engine = SyncEngine(config)
        fm = {"synctx": {"assignee": "alice"}}
        payload = engine._build_op_payload(fm, "body", "task.md")
        assert "/api/v3/users/42" in payload["_links"]["assignee"]["href"]

    def test_assignee_unknown_no_error(self):
        config = _make_config(
            targets=TargetsConfig(xwiki=_xwiki_config(), openproject=_op_config()),
        )
        engine = SyncEngine(config)
        fm = {"synctx": {"assignee": "unknown-user"}}
        payload = engine._build_op_payload(fm, "body", "task.md")
        assert "assignee" not in payload.get("_links", {})


class TestRetryConfig:
    def test_xwiki_retry(self):
        config = _make_config(
            targets=TargetsConfig(
                xwiki=XWikiTargetConfig(
                    base_url="https://xwiki.internal",
                    auth=TargetAuthConfig(type="basic", credential_env="X"),
                    retry=TargetRetryConfig(max_attempts=5, backoff_seconds=[1, 2, 4]),
                )
            ),
        )
        engine = SyncEngine(config)
        rc = engine._get_retry_config("xwiki")
        assert rc.max_attempts == 5

    def test_default_retry(self):
        config = _make_config()
        engine = SyncEngine(config)
        rc = engine._get_retry_config("openproject")
        assert rc.max_attempts == 3


@pytest.mark.asyncio
class TestProcessWithRetry:
    async def test_success_first_try(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        config = _make_config(sync=SyncConfig(state_path=str(tmp_path)))
        engine = SyncEngine(config)
        engine._process_single_file = AsyncMock(return_value="xwiki:docs.a")

        report = SyncReport()
        state = RepoState(repo_id="test-repo")
        retry_cfg = TargetRetryConfig(max_attempts=3, backoff_seconds=[0, 0])
        repo = _make_repo()

        result, error_type = await engine._process_with_retry(
            repo, MagicMock(), "A", "docs/a.md", "xwiki", "1",
            retry_cfg, report, state, "head123", "sha256abc",
        )
        assert result is True
        assert error_type == ""
        assert report.success_count == 1
        assert "docs/a.md" in state.synced_files

    async def test_retry_then_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        config = _make_config(sync=SyncConfig(state_path=str(tmp_path)))
        engine = SyncEngine(config)

        call_count = 0

        async def flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient timeout error")
            return "xwiki:docs.a"

        engine._process_single_file = flaky

        report = SyncReport()
        state = RepoState(repo_id="test-repo")
        retry_cfg = TargetRetryConfig(max_attempts=3, backoff_seconds=[0, 0])
        repo = _make_repo()

        result, _ = await engine._process_with_retry(
            repo, MagicMock(), "A", "docs/a.md", "xwiki", "1",
            retry_cfg, report, state, "head123", "sha256abc",
        )
        assert result is True
        assert call_count == 3

    async def test_all_retries_exhausted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        config = _make_config(sync=SyncConfig(state_path=str(tmp_path)))
        engine = SyncEngine(config)
        engine._process_single_file = AsyncMock(side_effect=RuntimeError("connection timeout"))

        report = SyncReport()
        state = RepoState(repo_id="test-repo")
        retry_cfg = TargetRetryConfig(max_attempts=2, backoff_seconds=[0])
        repo = _make_repo()

        result, error_type = await engine._process_with_retry(
            repo, MagicMock(), "A", "docs/a.md", "xwiki", "1",
            retry_cfg, report, state, "head123", "sha256abc",
        )
        assert result is False
        assert error_type == "transient_error"
        assert report.fail_count == 1
        assert len(state.pending_failures) == 1

    async def test_auth_error_no_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        config = _make_config(sync=SyncConfig(state_path=str(tmp_path)))
        engine = SyncEngine(config)
        engine._process_single_file = AsyncMock(side_effect=RuntimeError("401 Unauthorized"))

        report = SyncReport()
        state = RepoState(repo_id="test-repo")
        retry_cfg = TargetRetryConfig(max_attempts=3, backoff_seconds=[0, 0])
        repo = _make_repo()

        result, error_type = await engine._process_with_retry(
            repo, MagicMock(), "A", "docs/a.md", "xwiki", "1",
            retry_cfg, report, state, "head123", "sha256abc",
        )
        assert result is False
        assert error_type == "auth_error"
        engine._process_single_file.assert_called_once()


@pytest.mark.asyncio
class TestOnDeletePolicy:
    async def _make_engine_with_delete(self, tmp_path, monkeypatch, on_delete):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        monkeypatch.setenv("OP_TOKEN", "tok")
        config = _make_config(
            sync=SyncConfig(state_path=str(tmp_path), on_delete=on_delete),
            targets=TargetsConfig(
                xwiki=_xwiki_config(),
                openproject=_op_config(),
            ),
        )
        engine = SyncEngine(config)
        engine.xwiki_client = MagicMock()
        engine.xwiki_client.delete_page = AsyncMock()
        engine.op_client = MagicMock()
        engine.op_client.mark_deleted = AsyncMock(return_value=True)
        return engine

    async def test_ignore_skips_delete(self, tmp_path, monkeypatch):
        engine = await self._make_engine_with_delete(tmp_path, monkeypatch, "ignore")
        repo = _make_repo()
        await engine._process_single_file(repo, MagicMock(), "D", "docs/a.md", "xwiki", "1")
        engine.xwiki_client.delete_page.assert_not_called()

    async def test_mark_skips_xwiki_delete(self, tmp_path, monkeypatch):
        engine = await self._make_engine_with_delete(tmp_path, monkeypatch, "mark")
        repo = _make_repo()
        await engine._process_single_file(repo, MagicMock(), "D", "docs/a.md", "xwiki", "1")
        engine.xwiki_client.delete_page.assert_not_called()

    async def test_delete_calls_xwiki_delete(self, tmp_path, monkeypatch):
        engine = await self._make_engine_with_delete(tmp_path, monkeypatch, "delete")
        repo = _make_repo()
        await engine._process_single_file(repo, MagicMock(), "D", "docs/a.md", "xwiki", "1")
        engine.xwiki_client.delete_page.assert_called_once()

    async def test_mark_calls_op_mark_deleted(self, tmp_path, monkeypatch):
        engine = await self._make_engine_with_delete(tmp_path, monkeypatch, "mark")
        repo = _make_repo()
        await engine._process_single_file(repo, MagicMock(), "D", "issues/t.md", "openproject", "42")
        engine.op_client.mark_deleted.assert_called_once()

    async def test_delete_falls_back_to_op_mark(self, tmp_path, monkeypatch):
        engine = await self._make_engine_with_delete(tmp_path, monkeypatch, "delete")
        repo = _make_repo()
        await engine._process_single_file(repo, MagicMock(), "D", "issues/t.md", "openproject", "42")
        engine.op_client.mark_deleted.assert_called_once()


@pytest.mark.asyncio
class TestXWikiMermaidPublish:
    async def test_mermaid_block_uploaded_as_svg_attachment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XWIKI_CRED", "user:pass")
        monkeypatch.setattr(
            "sync_converters.mermaid.convert_mermaid_to_svg",
            lambda code: b"<svg>stub</svg>",
        )
        engine = SyncEngine(_make_config(sync=SyncConfig(state_path=str(tmp_path))))
        engine.xwiki_client = MagicMock()
        engine.xwiki_client.upload_attachment = AsyncMock()
        engine.xwiki_client.upsert_page = AsyncMock()
        git_manager = MagicMock()
        git_manager.read_file_text.return_value = (
            "---\ntitle: 개요\n---\n# 개요\n\n```mermaid\ngraph LR\n  A --> B\n```\n"
        )

        resource_id = await engine._process_single_file(
            _make_repo(), git_manager, "A", "docs/overview.md", "xwiki", "1",
        )

        assert resource_id == "xwiki:docs.overview"
        engine.xwiki_client.upload_attachment.assert_awaited_once_with(
            "docs", "overview", "mermaid_0.svg", b"<svg>stub</svg>", "image/svg+xml",
        )
        body = engine.xwiki_client.upsert_page.await_args.args[3]
        assert "![Mermaid Diagram](mermaid_0.svg)" in body
        assert "```mermaid" not in body
