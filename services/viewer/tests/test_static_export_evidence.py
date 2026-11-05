"""내보내기 근거 발췌에도 대상 저장소의 경로 제한을 적용한다."""

import importlib

import pytest

from test_static_export_crawl import (
    SECRET, _config, _crawl, _runtime, _isolate_viewer_environment,
    everything, make_source,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_repo", [False, True])
@pytest.mark.parametrize("restriction", ["exclude", "include", "hidden"])
async def test_evidence_reads_respect_target_repo_allowlist(tmp_path, monkeypatch, cross_repo, restriction):
    target_id = "repo-b" if cross_repo else "repo-a"
    private_dir = ".private" if restriction == "hidden" else "private"
    meeting = (
        '---\nsynctx:\n  type: meeting\n  meeting:\n    id: m1\n    title: Meeting\n'
        '    starts_at: "2026-10-24T10:00:00+09:00"\n    channel: c\n'
        f'    references:\n      /target/: "{target_id}:"\n'
        f'---\n# Meeting\n\n[Private](/target/{private_dir}/secret.md)\n\n[Public](/target/public/open.md)\n'
    )
    target_files = {f"{private_dir}/secret.md": f"# Private\n\n{SECRET}\n",
                    "public/open.md": "# Public\n\nVISIBLE-EVIDENCE-123\n"}
    rules = {"exclude": {"exclude": ["private/"]}, "include": {"include": ["public/"]}, "hidden": {}}[restriction]
    if cross_repo:
        source = make_source(tmp_path, {"public/meeting.md": meeting})
        target = make_source(tmp_path, target_files, "target")
        repos = [{"id": "repo-a", "name": "A", "url": str(source)},
                 {"id": target_id, "name": "B", "url": str(target), **rules}]
    else:
        source = make_source(tmp_path, {"public/meeting.md": meeting, **target_files})
        repos = [{"id": "repo-a", "name": "A", "url": str(source), **rules}]
    site = _config().SiteConfig(title="Test", repositories=repos)
    viewer = _runtime().load_viewer(_runtime().write_runtime_config(site, tmp_path / "cache"))
    original_reader = viewer._evidence_reader
    manager_class = importlib.import_module("synctx_common.git_manager").GitRepoManager
    original_read = manager_class.read_resolved
    reads = []

    def track_read(self, path, ref):
        reads.append((self.profile.id, path))
        return original_read(self, path, ref)

    monkeypatch.setattr(manager_class, "read_resolved", track_read)
    result = await _crawl().crawl(viewer, site)
    page = result.pages["/view/repo-a/public/meeting.md"]
    assert "VISIBLE-EVIDENCE-123" in page
    assert SECRET not in everything(result)
    assert (target_id, f"{private_dir}/secret.md") not in reads
    assert viewer._evidence_reader is original_reader
