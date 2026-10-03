"""Harness Validator 단위 테스트."""

import json

import pytest

from synctx_common.config import SynctxConfig
from synctx_common.harness import HarnessValidator


@pytest.fixture
def workspace(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "system_prompt.md").write_text("# System Prompt")
    (ctx / "project_state.yaml").write_text("phase: 1")
    (ctx / "glossary.md").write_text("# Glossary")
    (ctx / "decision-log.md").write_text("# Decisions")
    blueprints = ctx / "blueprints"
    blueprints.mkdir()
    reports = tmp_path / "reports" / "harness"
    reports.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def config():
    return SynctxConfig()


class TestContextFiles:
    def test_all_present_passes(self, workspace, config):
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        assert report["status"] == "success"
        assert len(report["errors"]) == 0

    def test_missing_file_fails(self, workspace, config):
        (workspace / ".context" / "glossary.md").unlink()
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        assert report["status"] == "failed"
        assert any("glossary.md" in e for e in report["errors"])


class TestAgentReadiness:
    def _write_doc(self, workspace, content):
        doc = workspace / "test-doc.md"
        doc.write_text(content)
        return doc

    def test_missing_title(self, workspace, config):
        self._write_doc(workspace, "No title here\n## 요약\nSome text\n## 결정사항\n- decided")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 1
        assert any("Title" in e for e in failures[0]["errors"])

    def test_missing_summary(self, workspace, config):
        self._write_doc(workspace, "# Title\nContent\n## 결정사항\n- decided")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 1
        assert any("Summary" in e for e in failures[0]["errors"])

    def test_todo_without_owner(self, workspace, config):
        self._write_doc(workspace, "# Title\n## 요약\nX\n## 결정사항\nY\n- [ ] do something")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 1
        assert any("owner" in e for e in failures[0]["errors"])

    def test_todo_with_owner_passes(self, workspace, config):
        self._write_doc(workspace, "# Title\n## 요약\nX\n## 결정사항\nY\n- [ ] @user do something")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 0

    def test_empty_mermaid_block(self, workspace, config):
        self._write_doc(workspace, "# Title\n## 요약\nX\n## 결정사항\nY\n```mermaid\n```\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 1
        assert any("Empty mermaid" in e for e in failures[0]["errors"])

    def test_valid_mermaid_passes(self, workspace, config):
        self._write_doc(workspace, "# Title\n## 요약\nX\n## 결정사항\nY\n```mermaid\ngraph TD\n  A-->B\n```\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "test-doc.md"]
        assert len(failures) == 0


class TestLinkValidation:
    def test_broken_link_detected(self, workspace, config):
        doc = workspace / "doc.md"
        doc.write_text("# Title\n## 요약\nX\n## 결정사항\nY\n[link](nonexistent.md)\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "doc.md"]
        assert len(failures) == 1
        assert any("Broken link" in e for e in failures[0]["errors"])

    def test_valid_link_passes(self, workspace, config):
        target = workspace / "target.md"
        target.write_text("# Target\n## 요약\nX\n## 결정사항\nY\n")
        doc = workspace / "doc.md"
        doc.write_text("# Title\n## 요약\nX\n## 결정사항\nY\n[link](target.md)\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "doc.md"]
        assert len(failures) == 0

    def test_external_url_ignored(self, workspace, config):
        doc = workspace / "doc.md"
        doc.write_text("# Title\n## 요약\nX\n## 결정사항\nY\n[link](https://example.com)\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "doc.md"]
        assert len(failures) == 0

    def test_anchor_link_ignored(self, workspace, config):
        doc = workspace / "doc.md"
        doc.write_text("# Title\n## 요약\nX\n## 결정사항\nY\n[link](#section)\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "doc.md"]
        assert len(failures) == 0

    def test_link_check_disabled(self, workspace, config):
        config.harness.agent_readiness.check_links = False
        doc = workspace / "doc.md"
        doc.write_text("# Title\n## 요약\nX\n## 결정사항\nY\n[link](missing.md)\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        failures = [f for f in report["agent_readiness_failures"] if f["file"] == "doc.md"]
        assert len(failures) == 0


class TestVersionConsistency:
    def test_task_not_in_version_issues(self, workspace, config):
        v_doc = workspace / "version.md"
        v_doc.write_text("---\nsynctx:\n  type: version\n  name: sprint-1\n  issues:\n    - other.md\n---\n# Version\n## 요약\nX\n## 결정사항\nY\n")
        t_doc = workspace / "task.md"
        t_doc.write_text("---\nsynctx:\n  type: task\n  version: sprint-1\n---\n# Task\n## 요약\nX\n## 결정사항\nY\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        assert any("not listed" in e for e in report["errors"])

    def test_unknown_version_warned(self, workspace, config):
        t_doc = workspace / "task.md"
        t_doc.write_text("---\nsynctx:\n  type: task\n  version: sprint-99\n---\n# Task\n## 요약\nX\n## 결정사항\nY\n")
        v = HarnessValidator(config, str(workspace))
        report = v.validate()
        assert any("sprint-99" in w for w in report["warnings"])


class TestReportOutput:
    def test_json_and_md_written(self, workspace, config):
        v = HarnessValidator(config, str(workspace))
        v.validate()
        reports_dir = workspace / "reports" / "harness"
        assert (reports_dir / "latest.json").exists()
        assert (reports_dir / "latest.md").exists()
        data = json.loads((reports_dir / "latest.json").read_text())
        assert "status" in data
        assert "files_checked" in data


MEETING_DOC = """---
synctx:
  type: meeting
  meeting:
    id: {meeting_id}
    title: 회의
    starts_at: {starts_at}
    channel: {channel}
---
# 회의

## 요약

내용
"""


class TestMeetingDocuments:
    def _write(self, workspace, name, meeting_id="m-1", starts_at="2026-10-08T14:00+09:00", channel="dx-team"):
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MEETING_DOC.format(meeting_id=meeting_id, starts_at=starts_at, channel=channel))
        return path

    def test_valid_meeting_has_no_meeting_errors(self, workspace, config):
        self._write(workspace, "meetings/a.md")
        report = HarnessValidator(config, str(workspace)).validate()
        assert not any("meeting" in e or "starts_at" in e for e in report["errors"])

    def test_invalid_meeting_reports_path_and_error(self, workspace, config):
        self._write(workspace, "meetings/a.md", starts_at="2026-10-08T14:00")
        report = HarnessValidator(config, str(workspace)).validate()
        assert "meetings/a.md: starts_at에 타임존이 없습니다(예: 2026-10-08T14:00+09:00)" in report["errors"]
        assert report["status"] == "failed"

    def test_duplicate_meeting_id(self, workspace, config):
        self._write(workspace, "meetings/a.md", meeting_id="same")
        self._write(workspace, "meetings/b.md", meeting_id="same")
        report = HarnessValidator(config, str(workspace)).validate()
        assert "meeting.id 'same'가 여러 문서에 있습니다: meetings/a.md, meetings/b.md" in report["errors"]

    def test_unmapped_channel_is_not_checked(self, workspace, config):
        self._write(workspace, "meetings/a.md", channel="no-such-channel")
        report = HarnessValidator(config, str(workspace)).validate()
        assert not any("channel" in e for e in report["errors"])
